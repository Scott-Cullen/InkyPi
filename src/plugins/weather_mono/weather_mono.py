import logging
import os
from datetime import datetime

from plugins.weather.weather import Weather, pytz

logger = logging.getLogger(__name__)


class WeatherMono(Weather):
    ICON_TOKEN_MAP = {
        "01d": "wb_sunny",
        "01n": "dark_mode",
        "02d": "cloud",
        "02n": "cloud",
        "022d": "wb_sunny",
        "022n": "dark_mode",
        "04d": "cloud",
        "09d": "water_drop",
        "10d": "cloud",
        "10n": "cloud",
        "11d": "bolt",
        "13d": "ac_unit",
        "48d": "cloud",
        "50d": "cloud",
        "51d": "water_drop",
        "53d": "water_drop",
        "56d": "ac_unit",
        "57d": "ac_unit",
        "71d": "ac_unit",
        "73d": "ac_unit",
        "77d": "cloud",
        "newmoon": "brightness_2",
        "waxingcrescent": "brightness_2",
        "firstquarter": "brightness_2",
        "waxinggibbous": "brightness_2",
        "fullmoon": "brightness_3",
        "waninggibbous": "brightness_2",
        "lastquarter": "brightness_2",
        "waningcrescent": "brightness_2",
        "sunrise": "light_mode",
        "sunset": "dark_mode",
        "wind": "air",
        "humidity": "water_drop",
        "pressure": "compress",
        "uvi": "light_mode",
        "visibility": "visibility",
        "aqi": "air",
    }

    def _icon_name_from_path(self, icon_path):
        if not icon_path:
            return ""
        return os.path.splitext(os.path.basename(icon_path))[0]

    def _token_for_icon(self, icon_path):
        icon_name = self._icon_name_from_path(icon_path)
        return self.ICON_TOKEN_MAP.get(icon_name, "cloud")

    def _attach_material_symbol_tokens(self, template_params):
        template_params["current_icon_token"] = self._token_for_icon(template_params.get("current_day_icon"))

        for forecast_item in template_params.get("forecast", []):
            forecast_item["icon_token"] = self._token_for_icon(forecast_item.get("icon"))
            forecast_item["moon_icon_token"] = self._token_for_icon(forecast_item.get("moon_phase_icon"))

        for data_point in template_params.get("data_points", []):
            data_point["icon_token"] = self._token_for_icon(data_point.get("icon"))

    def _validate_symbol_font(self):
        font_path = self.get_plugin_dir("icons/MaterialSymbolsOutlined.ttf")
        if not os.path.isfile(font_path):
            logger.warning("Weather Mono symbol font not found: %s", font_path)

    def generate_image(self, settings, device_config):
        lat = float(settings.get('latitude'))
        long = float(settings.get('longitude'))
        if not lat or not long:
            raise RuntimeError("Latitude and Longitude are required.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        weather_provider = settings.get('weatherProvider', 'OpenWeatherMap')
        title = settings.get('customTitle', '')

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone)

        try:
            if weather_provider == "OpenWeatherMap":
                api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
                if not api_key:
                    raise RuntimeError("Open Weather Map API Key not configured.")
                weather_data = self.get_weather_data(api_key, units, lat, long)
                aqi_data = self.get_air_quality(api_key, lat, long)
                if settings.get('titleSelection', 'location') == 'location':
                    title = self.get_location(api_key, lat, long)
                if settings.get('weatherTimeZone', 'locationTimeZone') == 'locationTimeZone':
                    logger.info("Using location timezone for OpenWeatherMap data.")
                    wtz = self.parse_timezone(weather_data)
                    template_params = self.parse_weather_data(weather_data, aqi_data, wtz, units, time_format, lat)
                else:
                    logger.info("Using configured timezone for OpenWeatherMap data.")
                    template_params = self.parse_weather_data(weather_data, aqi_data, tz, units, time_format, lat)
            elif weather_provider == "OpenMeteo":
                forecast_days = 7
                weather_data = self.get_open_meteo_data(lat, long, units, forecast_days + 1)
                aqi_data = self.get_open_meteo_air_quality(lat, long)
                template_params = self.parse_open_meteo_data(weather_data, aqi_data, tz, units, time_format, lat)
            else:
                raise RuntimeError(f"Unknown weather provider: {weather_provider}")

            template_params['title'] = title
        except Exception as error:
            logger.error(f"{weather_provider} request failed: {str(error)}")
            raise RuntimeError(f"{weather_provider} request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        template_params["plugin_settings"] = settings

        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh_time = now.strftime("%Y-%m-%d %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time

        self._attach_material_symbol_tokens(template_params)
        self._validate_symbol_font()

        first_image = self.render_image(dimensions, "weather_mono.html", "weather_mono.css", template_params)
        if not first_image:
            raise RuntimeError("Failed to take screenshot, please check logs.")

        second_image = self.render_image(dimensions, "weather_mono.html", "weather_mono.css", template_params)
        if second_image:
            return second_image

        return first_image
