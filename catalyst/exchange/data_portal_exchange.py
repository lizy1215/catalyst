#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import os
from time import sleep

import collections
import pandas as pd
from catalyst.assets._assets import TradingPair
from logbook import Logger

from catalyst.data.bundles.core import load, from_bundle_ingest_dirname, \
    BundleData, minute_path, five_minute_path, daily_path
from catalyst.data.data_portal import DataPortal
from catalyst.data.five_minute_bars import BcolzFiveMinuteBarReader
from catalyst.data.minute_bars import BcolzMinuteBarReader
from catalyst.data.us_equity_pricing import BcolzDailyBarReader
from catalyst.exchange.exchange_errors import (
    ExchangeRequestError,
    ExchangeBarDataError,
    BundleNotFoundError)
from catalyst.utils.paths import data_path

log = Logger('DataPortalExchange')


class DataPortalExchangeBase(DataPortal):
    def __init__(self, *args, **kwargs):

        self.exchanges = kwargs.pop('exchanges', None)
        # TODO: put somewhere accessible by each algo
        self.retry_get_history_window = 5
        self.retry_get_spot_value = 5
        self.retry_delay = 5

        super(DataPortalExchangeBase, self).__init__(*args, **kwargs)

    def _get_history_window(self,
                            assets,
                            end_dt,
                            bar_count,
                            frequency,
                            field,
                            data_frequency,
                            ffill=True,
                            attempt_index=0):
        try:
            exchange_assets = dict()
            for asset in assets:
                if asset.exchange not in exchange_assets:
                    exchange_assets[asset.exchange] = list()

                exchange_assets[asset.exchange].append(asset)

            if len(exchange_assets) > 1:
                df_list = []
                for exchange_name in exchange_assets:
                    exchange = self.exchanges[exchange_name]
                    assets = exchange_assets[exchange_name]

                    df_exchange = self.get_exchange_history_window(
                        exchange,
                        assets,
                        end_dt,
                        bar_count,
                        frequency,
                        field,
                        data_frequency,
                        ffill)

                    df_list.append(df_exchange)

                # Merging the values values of each exchange
                return pd.concat(df_list)

            else:
                exchange = self.exchanges[exchange_assets.keys()[0]]
                return exchange.get_history_window(
                    assets,
                    end_dt,
                    bar_count,
                    frequency,
                    field,
                    data_frequency,
                    ffill)

        except ExchangeRequestError as e:
            log.warn(
                'get history attempt {}: {}'.format(attempt_index, e)
            )
            if attempt_index < self.retry_get_history_window:
                sleep(self.retry_delay)
                return self._get_history_window(assets,
                                                end_dt,
                                                bar_count,
                                                frequency,
                                                field,
                                                data_frequency,
                                                ffill,
                                                attempt_index + 1)
            else:
                raise ExchangeBarDataError(
                    data_type='history',
                    attempts=attempt_index,
                    error=e
                )

    def get_history_window(self,
                           assets,
                           end_dt,
                           bar_count,
                           frequency,
                           field,
                           data_frequency=None,
                           ffill=True):
        return self._get_history_window(assets,
                                        end_dt,
                                        bar_count,
                                        frequency,
                                        field,
                                        data_frequency,
                                        ffill)

    @abc.abstractmethod
    def get_exchange_history_window(self,
                                    exchange,
                                    assets,
                                    end_dt,
                                    bar_count,
                                    frequency,
                                    field,
                                    data_frequency,
                                    ffill=True):
        pass

    def _get_spot_value(self, assets, field, dt, data_frequency,
                        attempt_index=0):
        try:
            if isinstance(assets, TradingPair):
                exchange = self.exchanges[assets.exchange]
                return self.get_exchange_spot_value(
                    exchange, assets, field, dt, data_frequency)

            else:
                exchange_assets = dict()
                for asset in assets:
                    if asset.exchange not in exchange_assets:
                        exchange_assets[asset.exchange] = list()

                    exchange_assets[asset.exchange].append(asset)

                if len(exchange_assets.keys()) == 1:
                    exchange = self.exchanges[exchange_assets.keys()[0]]
                    return self.get_exchange_spot_value(
                        exchange, assets, field, dt, data_frequency)

                else:
                    spot_values = []
                    for exchange_name in exchange_assets:
                        exchange = self.exchanges[exchange_name]
                        assets = exchange_assets[exchange_name]
                        exchange_spot_values = self.get_exchange_spot_value(
                            exchange,
                            assets,
                            field,
                            dt,
                            data_frequency
                        )
                        if len(assets) == 1:
                            spot_values.append(exchange_spot_values)
                        else:
                            spot_values += exchange_spot_values

                    return spot_values

        except ExchangeRequestError as e:
            log.warn(
                'get spot value attempt {}: {}'.format(attempt_index, e)
            )
            if attempt_index < self.retry_get_spot_value:
                sleep(self.retry_delay)
                return self._get_spot_value(assets, field, dt, data_frequency,
                                            attempt_index + 1)
            else:
                raise ExchangeBarDataError(
                    data_type='spot',
                    attempts=attempt_index,
                    error=e
                )

    def get_spot_value(self, assets, field, dt, data_frequency):
        return self._get_spot_value(assets, field, dt, data_frequency)

    @abc.abstractmethod
    def get_exchange_spot_value(self, exchange, assets, field, dt,
                                data_frequency):
        return

    def get_adjusted_value(self, asset, field, dt,
                           perspective_dt,
                           data_frequency,
                           spot_value=None):
        # TODO: does this pertain to cryptocurrencies?
        log.warn('get_adjusted_value is not implemented yet!')
        return spot_value


class DataPortalExchangeLive(DataPortalExchangeBase):
    def __init__(self, *args, **kwargs):
        super(DataPortalExchangeLive, self).__init__(*args, **kwargs)

    def get_exchange_history_window(self,
                                    exchange,
                                    assets,
                                    end_dt,
                                    bar_count,
                                    frequency,
                                    field,
                                    data_frequency,
                                    ffill=True):
        df = exchange.get_history_window(
            assets,
            end_dt,
            bar_count,
            frequency,
            field,
            data_frequency,
            ffill)
        return df

    def get_exchange_spot_value(self, exchange, assets, field, dt,
                                data_frequency):
        exchange_spot_values = exchange.get_spot_value(
            assets, field, dt, data_frequency)

        return exchange_spot_values


class DataPortalExchangeBacktest(DataPortalExchangeBase):
    def __init__(self, *args, **kwargs):

        super(DataPortalExchangeBacktest, self).__init__(*args, **kwargs)

        self.daily_bar_readers = dict()
        self.minute_bar_readers = dict()
        self.five_minute_bar_readers = dict()
        for exchange_name in self.exchanges:
            name = 'exchange_{}'.format(exchange_name)
            time_folder = \
                DataPortalExchangeBacktest.find_most_recent_time(name)

            if time_folder is None:
                raise BundleNotFoundError(exchange=exchange_name)

            self.daily_bar_readers[exchange_name] = \
                BcolzDailyBarReader(
                    daily_path(name, time_folder),
                )

            self.five_minute_bar_readers[exchange_name] = \
                BcolzFiveMinuteBarReader(
                    five_minute_path(name, time_folder),
                )

            self.minute_bar_readers[exchange_name] = \
                BcolzMinuteBarReader(
                    minute_path(name, time_folder),
                )

    @staticmethod
    def find_most_recent_time(bundle_name):
        try:
            bundle_folders = os.listdir(
                data_path([bundle_name]),
            )
        except OSError:
            return None

        most_recent_bundle = dict()
        for folder in bundle_folders:
            date = from_bundle_ingest_dirname(folder)
            if not most_recent_bundle or date > \
                    most_recent_bundle[most_recent_bundle.keys()[0]]:
                most_recent_bundle = dict()
                most_recent_bundle[folder] = date

        if most_recent_bundle:
            return most_recent_bundle.keys()[0]
        else:
            return None

    def get_exchange_history_window(self,
                                    exchange,
                                    assets,
                                    end_dt,
                                    bar_count,
                                    frequency,
                                    field,
                                    data_frequency,
                                    ffill=True):
        df = exchange.get_history_window(
            assets,
            end_dt,
            bar_count,
            frequency,
            field,
            data_frequency,
            ffill)
        return df

    def get_exchange_spot_value(self, exchange, assets, field, dt,
                                data_frequency):

        if data_frequency == 'minute':
            reader = self.minute_bar_readers[exchange.name]
        elif data_frequency == '5-minute':
            reader = self.five_minute_bar_readers[exchange.name]
        elif data_frequency == 'daily':
            reader = self.daily_bar_readers[exchange.name]
        else:
            raise ValueError('Unsupported frequency')

        values = []
        for asset in assets:
            try:
                value = reader.get_value(
                    sid=asset.sid,
                    dt=dt,
                    field=field
                )
                values.append(value)
            except Exception as e:
                log.warn('minute data not found: {}'.format(e))
                values.append(None)

        if len(assets) == 1:
            return values[0]
        else:
            return values