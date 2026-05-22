#!/usr/bin/env python3
"""
ring_cycle.py � NeoPixel ring brightness cycler
------------------------------------------------
Button wired: GPIO 14 ? button ? GND  (any GPIO pin works for a button)
Ring data:    GPIO 18 ? 470O ? DIN    (GPIO 18 required for NeoPixel timing)
Ring power:   external 5V supply, GND tied to Pi GND

Each button press advances: off ? dim ? medium ? bright ? off ? ...

Run with sudo (NeoPixel needs hardware PWM access):
    sudo python3 ring_cycle.py

Install dependencies if needed:
    sudo pip3 install adafruit-circuitpython-neopixel --break-system-packages
"""

import board
import neopixel
from gpiozero import Button
from signal import pause

# -- Configuration ------------------------------------------------------------

NUM_PIXELS  = 24          # match your ring (24-pixel WS2812B)
BUTTON_PIN  = 6          # GPIO pin your button is wired to
WARM_WHITE  = (255, 200, 120)   # warm white � tweak RGB to taste

# Brightness levels: fraction of full brightness (0.0 = off, 1.0 = full)
LEVELS = [
    ("off",    0.0),
    ("dim",    0.2),
    ("medium", 0.5),
    ("bright", 1.0),
]

# -- Setup ---------------------------------------------------------------------

ring = neopixel.NeoPixel(
    board.D18,          # GPIO 18 � do not change, required for WS2812 timing
    NUM_PIXELS,
    brightness=1.0,     # we scale color manually so keep this at 1.0
    auto_write=False,   # only push to ring when we call ring.show()
)

current_level = 0       # index into LEVELS, starts at "off"

# -- Helpers -------------------------------------------------------------------

def scaled_color(base_color, brightness):
    """Scale an RGB tuple by a brightness fraction (0.0 � 1.0)."""
    r, g, b = base_color
    return (int(r * brightness), int(g * brightness), int(b * brightness))

def apply_level():
    """Push the current brightness level to the ring."""
    label, brightness = LEVELS[current_level]
    color = scaled_color(WARM_WHITE, brightness)
    ring.fill(color)
    ring.show()
    print(f"Ring light ? {label} ({int(brightness * 100)}%)")

def on_button_press():
    """Advance to the next level and update the ring."""
    global current_level
    current_level = (current_level + 1) % len(LEVELS)
    apply_level()

# -- Main ----------------------------------------------------------------------

button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)
button.when_pressed = on_button_press

apply_level()   # start with ring off
print("Ready � press the button to cycle brightness. Ctrl+C to quit.")
pause()         # keep the script running, listening for button presses
