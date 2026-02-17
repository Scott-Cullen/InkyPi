import inspect
import importlib
import logging
import sys
import time

from display.abstract_display import AbstractDisplay
from PIL import Image
from pathlib import Path
from plugins.plugin_registry import get_plugin_instance

logger = logging.getLogger(__name__)


def split_image_for_bi_color_epd(image):
    """
    Convert image into two 1-bit layers for bi-color (black and red) e-paper displays.
    """
    black = (0, 0, 0)
    white = (255, 255, 255)
    red = (255, 0, 0)

    palette_data = [*black, *white, *red]
    palette_img = Image.new('P', (1, 1))
    palette_img.putpalette(palette_data)

    indexed_img = image.quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
    black_layer = indexed_img.point(lambda p: 0 if p == 0 else 1, mode='1')
    red_layer = indexed_img.point(lambda p: 0 if p == 2 else 1, mode='1')
    return black_layer, red_layer


class WaveshareDisplay(AbstractDisplay):
    """
    Handles Waveshare e-paper display dynamically based on device type.

    This class loads the appropriate display driver dynamically based on the
    `display_type` specified in the device configuration, allowing support for
    multiple Waveshare EPD models.

    The module drivers are in display.waveshare_epd.
    """

    def initialize_display(self):

        """
        Initializes the Waveshare display device.

        Retrieves the display type from the device configuration and dynamically
        loads the corresponding Waveshare EPD driver from display.waveshare_epd.

        Raises:
            ValueError: If `display_type` is missing or the specified module is
                        not found.
        """

        logger.info("Initializing Waveshare display")

        # Full clears can help reduce ghosting but are slow; throttle them.
        # This is in-memory state (resets when the app restarts).
        self._full_clear_interval_s = 60 * 60
        self._last_full_clear_monotonic = None

        # get the device type which should be the model number of the device.
        display_type = self.device_config.get_config("display_type")
        logger.info(f"Loading EPD display for {display_type} display")

        if not display_type:
            raise ValueError("Waveshare driver but 'display_type' not specified in configuration.")

        # Construct module path dynamically - e.g. "display.waveshare_epd.epd7in3e"
        module_name = f"display.waveshare_epd.{display_type}"

        # Workaround for some Waveshare drivers using 'import epdconfig' causing import errors
        epd_dir = Path(__file__).parent / "waveshare_epd"
        if str(epd_dir) not in sys.path:
            sys.path.insert(0, str(epd_dir))

        try:
            # Dynamically load module
            epd_module = importlib.import_module(module_name)
            self.epd_display = epd_module.EPD()
            # Workaround for init functions with inconsistent casing.
            # For certain drivers (e.g. epd7in5_V2) we prefer init_fast() if available.
            if display_type == "epd7in5_V2":
                self.epd_display_init = getattr(
                    self.epd_display,
                    "init_fast",
                    getattr(self.epd_display, "Init", getattr(self.epd_display, "init", None)),
                )
            else:
                self.epd_display_init = getattr(self.epd_display, "Init", getattr(self.epd_display, "init", None))

            if not callable(self.epd_display_init):
                raise AttributeError("No Init/init method found")

            self.epd_display_init()

            display_args_spec = inspect.getfullargspec(self.epd_display.display)
        except ModuleNotFoundError:
            raise ValueError(f"Unsupported Waveshare display type: {display_type}")
        except AttributeError:
            raise ValueError(f"Display does not support required methods: {display_type}")

        self.bi_color_display = len(display_args_spec.args) > 2

        # update the resolution directly from the loaded device context
        if not self.device_config.get_config("resolution"):
            w, h = int(self.epd_display.width), int(self.epd_display.height)
            resolution = [w, h] if w >= h else [h, w]
            self.device_config.update_value(
                "resolution",
                resolution,
                write=True)


    def display_image(self, image, image_settings=[]):

        """
        Displays an image on the Waveshare display.

        The image has been processed by adjusting orientation, resizing, and converting it
        into the buffer format required for e-paper rendering.

        Args:
            image (PIL.Image): The image to be displayed.
            image_settings (list, optional): Additional settings to modify image rendering.

        Raises:
            ValueError: If no image is provided.
        """

        logger.info("Displaying image to Waveshare display.")
        if not image:
            raise ValueError(f"No image provided.")

        logger.debug(
            "Waveshare frame info | mode=%s size=%sx%s expected=%sx%s",
            getattr(image, "mode", "unknown"),
            image.size[0] if getattr(image, "size", None) else "?",
            image.size[1] if getattr(image, "size", None) else "?",
            getattr(self.epd_display, "width", "?"),
            getattr(self.epd_display, "height", "?"),
        )

        # Assume device was in sleep mode.
        self.epd_display_init()

        # Clear residual pixels occasionally (e.g. once per hour) to reduce ghosting.
        now = time.monotonic()
        should_full_clear = (
            self._last_full_clear_monotonic is None
            or (now - self._last_full_clear_monotonic) >= self._full_clear_interval_s
        )
        if should_full_clear:
            logger.info("Performing full clear on Waveshare display.")
            self.epd_display.Clear()
            # Some panels/controllers can intermittently fail to latch the next frame
            # immediately after a full clear unless reinitialized.
            logger.info("Reinitializing Waveshare display after full clear.")
            self.epd_display_init()
            self._last_full_clear_monotonic = now
        else:
            logger.debug("Skipping full clear (throttled).")

        # Display the image on the WS display.
        if not self.bi_color_display:
            self.epd_display.display(self.epd_display.getbuffer(image))
        else:
            black_layer, red_layer = split_image_for_bi_color_epd(image)

            self.epd_display.display(
                self.epd_display.getbuffer(black_layer),
                self.epd_display.getbuffer(red_layer),
            )

        # Give the controller a short settle window before deep sleep.
        time.sleep(0.2)

        # Put device into low power mode (EPD displays maintain image when powered off)
        logger.info("Putting Waveshare display into sleep mode for power saving.")
        self.epd_display.sleep()
