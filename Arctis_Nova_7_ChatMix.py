"""   Copyright (C) 2022  birdybirdonline & awth13 - see LICENSE.md
    @ https://github.com/birdybirdonline/Linux-Arctis-7-Plus-ChatMix

    Contact via Github in the first instance
    https://github.com/birdybirdonline
    https://github.com/awth13

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
    """

from __future__ import annotations
import os
import sys
import signal
import logging
import re
from typing import TYPE_CHECKING, cast

import usb.core

if TYPE_CHECKING:
    from typing import NoReturn


class Arctis7PlusChatMix:
    sinks_created = False

    def __init__(self):

        # set to receive signal from systemd for termination
        signal.signal(signal.SIGTERM, self.__handle_sigterm)

        self.log = self._init_log()
        self.log.info("Initializing ac7pcm...")

        # identify the arctis Nova 7 device
        try:
            dev=usb.core.find(idVendor=0x1038, idProduct=0x2202)
        except Exception:
            dev = None

        if dev is None:
            self.log.error("""Failed to identify the Arctis Nova 7 device.
            Please ensure it is connected.\n
            Please note: This program only supports the 'Nova 7' model.""")
            self.die_gracefully(trigger ="Couldn't find arctis7 model")
        self.dev = cast(usb.core.Device, dev)

        # select its interface and USB endpoint, and capture the endpoint address
        try:
            # interface index 7 of the Arctis Nova 7 is the USB HID for the ChatMix dial;
            # its actual interface number on the device itself is 5.
            self.interface = self.dev[0].interfaces()[7]
            self.interface_num: int = self.interface.bInterfaceNumber  # type: ignore[annotation-unchecked]
            self.endpoint = self.interface.endpoints()[0]
            self.addr: str = self.endpoint.bEndpointAddress  # type: ignore[annotation-unchecked]

        except Exception:
            self.log.error("""Failure to identify relevant
            USB device's interface or endpoint. Shutting down...""")
            self.die_gracefully(trigger="identification of USB endpoint")

        # detach if the device is active
        if self.dev.is_kernel_driver_active(self.interface_num):
            self.dev.detach_kernel_driver(self.interface_num)

        self.VAC = self._init_VAC()

    def _init_log(self):
        log = logging.getLogger(__name__)
        log.setLevel(logging.DEBUG)
        stdout_handler = logging.StreamHandler()
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(logging.Formatter('%(levelname)8s | %(message)s'))
        log.addHandler(stdout_handler)    
        return (log)

    def _init_VAC(self):
        """Get name of default sink, establish virtual sink
        and pipe its output to the default sink
        """

        # get the default sink id from pactl
        self.system_default_sink = os.popen("pactl get-default-sink").read().strip()
        self.log.info(f"default sink identified as {self.system_default_sink}")

        # attempt to identify an Arctis sink via pactl
        try:
            pactl_short_sinks = os.popen("pactl list short sinks").readlines()
            # grab any elements from list of pactl sinks that are Arctis 7
            arctis = re.compile('.*[aA]rctis.*7')
            arctis_sink = list(filter(arctis.match, pactl_short_sinks))[0]

            # split the arctis line on tabs (which form table given by 'pactl short sinks')
            tabs_pattern = re.compile(r'\t')
            tabs_re = re.split(tabs_pattern, arctis_sink)

            # skip first element of tabs_re (sink's ID which is not persistent)
            arctis_device = tabs_re[1]
            self.log.info(f"Arctis sink identified as {arctis_device}")
            default_sink = arctis_device

        except Exception:
            self.log.error("""Something wrong with Arctis definition
            in pactl list short sinks regex matching.
            Likely no match found for device, check traceback.
            """, exc_info=True)
            self.die_gracefully(trigger="No Arctis device match")

        # Destroy virtual sinks if they already existed incase of previous failure:
        try:
            destroy_a7p_game = os.system("pw-cli destroy Arctis_Game 2>/dev/null")
            destroy_a7p_chat = os.system("pw-cli destroy Arctis_Chat 2>/dev/null")
            if destroy_a7p_game == 0 or destroy_a7p_chat == 0:
                raise Exception
        except Exception:
            self.log.info("""Attempted to destroy old VAC sinks at init but none existed""")

        # Instantiate our virtual sinks - Arctis_Chat and Arctis_Game
        try:
            self.log.info("Creating VACS...")
            os.system("""pw-cli create-node adapter '{
                factory.name=support.null-audio-sink
                node.name=Arctis_Game
                node.description="Arctis Nova 7 Game"
                media.class=Audio/Sink
                monitor.channel-volumes=true
                object.linger=true
                audio.position=[FL FR]
                }' 1>/dev/null
            """)

            os.system("""pw-cli create-node adapter '{
                factory.name=support.null-audio-sink
                node.name=Arctis_Chat
                node.description="Arctis Nova 7 Chat"
                media.class=Audio/Sink
                monitor.channel-volumes=true
                object.linger=true
                audio.position=[FL FR]
                }' 1>/dev/null
            """)
        except Exception:
            self.log.error("""Failure to create node adapter -
            Arctis_Chat virtual device could not be created""", exc_info=True)
            self.die_gracefully(trigger="VAC node adapter")
        self.sinks_created = True

        #route the virtual sink's L&R channels to the default system output's LR
        try:
            self.log.info("Assigning VAC sink monitors output to default device...")

            os.system(f'pw-link "Arctis_Game:monitor_FL" '
            f'"{default_sink}:playback_FL" 1>/dev/null')

            os.system(f'pw-link "Arctis_Game:monitor_FR" '
            f'"{default_sink}:playback_FR" 1>/dev/null')

            os.system(f'pw-link "Arctis_Chat:monitor_FL" '
            f'"{default_sink}:playback_FL" 1>/dev/null')

            os.system(f'pw-link "Arctis_Chat:monitor_FR" '
            f'"{default_sink}:playback_FR" 1>/dev/null')

        except Exception:
            self.log.error("""Couldn't create the links to
            pipe LR from VAC to default device""", exc_info=True)
            self.die_gracefully(trigger="LR links")

        # set the default sink to Arctis Game
        os.system('pactl set-default-sink Arctis_Game')

    def _del_VAC(self) -> None:
        os.system(f"pactl set-default-sink {self.system_default_sink}")

        if self.sinks_created:
            self.log.info("Destroying virtual sinks...")
            os.system("pw-cli destroy Arctis_Game 1>/dev/null")
            os.system("pw-cli destroy Arctis_Chat 1>/dev/null")
            self.sinks_created = False

    def start_modulator_signal(self):
        """Listen to the USB device for modulator knob's signal
        and adjust volume accordingly
        """

        self.log.info("Reading modulator USB input started")
        self.log.info("-"*45)
        self.log.info("Arctis Nova 7 ChatMix Enabled!")
        self.log.info("-"*45)
        while True:
            try:
                # read the input of the USB signal. Signal is sent in 64-bit interrupt packets.
                # read_input[1] returns value to use for default device volume
                # read_input[2] returns the value to use for virtual device volume
                read_input = self.dev.read(self.addr, 64)

                # 69 is the signal for the chatmix
                if read_input[0] == 69:
                    default_device_volume = "{}%".format(read_input[1])
                    virtual_device_volume = "{}%".format(read_input[2])

                    # os.system calls to issue the commands directly to pactl
                    os.system(f'pactl set-sink-volume Arctis_Game {default_device_volume}')
                    os.system(f'pactl set-sink-volume Arctis_Chat {virtual_device_volume}')
                # We have some options here, but 187 seems reasonable
                elif read_input[0] == 187:
                    if read_input[1] == 3:
                        self._init_VAC()
                    elif read_input[1] == 0:
                        self._del_VAC()
                    else:
                        self.log.warning(f"Unknown message value {read_input[1]}")
                else:
                    self.log.debug(f"Unhandled message {read_input}")

            except usb.core.USBTimeoutError:
                pass
            except usb.core.USBError:
                self.log.fatal("USB input/output error - likely disconnect")
                self._del_VAC()
                break

    def __handle_sigterm(self, sig, frame) -> NoReturn:
        self.die_gracefully()

    def die_gracefully(self, trigger=None) -> NoReturn:
        """Kill the process and remove the VACs
        on fatal exceptions or SIGTERM / SIGINT
        """

        self.log.info('Cleanup on shutdown')

        self._del_VAC()

        if trigger is not None:
            self.log.info("-"*45)
            self.log.fatal("Failure reason: " + trigger)
            self.log.info("-"*45)
            sys.exit(1)
        else:
            self.log.info("-"*45)
            self.log.info("Artcis Nova 7 ChatMix shut down gracefully... Bye Bye!")
            self.log.info("-"*45)
            sys.exit(0)


if __name__ == "__main__":
    a7pcm_service = Arctis7PlusChatMix()
    try:
        a7pcm_service.start_modulator_signal()
    except KeyboardInterrupt:
        a7pcm_service.die_gracefully()
    except Exception as exc:
        a7pcm_service.die_gracefully(trigger=str(exc))
