from __future__ import print_function
from usbrply import printer
from usbrply import parsers
from .printer import Printer, indented, indent_inc, indent_dec, indent_reset
import sys
import binascii
from . import usb
from .util import myord


def comment(s):
    indented("# %s" % (s, ))


def bytes2AnonArray(bytes_data):
    # In Python2 bytes_data is a string, in Python3 it's bytes.
    # The element type is different (string vs int) and we have to deal
    # with that when printing this number as hex.

    byte_str = "b\""

    for i in range(len(bytes_data)):
        if i and i % 16 == 0:
            byte_str += "\"\n            b\""
        byte_str += "\\x%02X" % (bytes_data[i], )
    return byte_str + "\""


class LibusbPyPrinter(Printer):
    def __init__(self, argsj, verbose=None):
        Printer.__init__(self, argsj)
        self.prevd = None
        self.wrapper = argsj.get("wrapper", False)
        self.sleep = argsj.get("sleep", False)
        self.packet_numbers = argsj.get("packet_numbers", True)
        # FIXME
        self.vid = None
        self.pid = None
        if verbose is None:
            verbose = argsj.get("verbose", False)
        self.verbose = verbose
        self.argsj = argsj

    def print_imports(self):
        print('''\
import binascii
import time
import usb1
''',
              file=printer.print_file)

    def print_wrapper_header(self):
        print('''\
def validate_read(expected, actual, msg):
    if expected != actual:
        print('Failed %s' % msg)
        print('  Expected; %s' % binascii.hexlify(expected,))
        print('  Actual:   %s' % binascii.hexlify(actual,))
        #raise Exception("failed validate: %s" % msg)

''',
              file=printer.print_file)
        print("def replay(dev):", file=printer.print_file)
        indent_inc()
        print('''\
    def bulkRead(endpoint, length, timeout=None):
        return dev.bulkRead(endpoint, length, timeout=(1000 if timeout is None else timeout))

    def bulkWrite(endpoint, data, timeout=None):
        dev.bulkWrite(endpoint, data, timeout=(1000 if timeout is None else timeout))
    
    def controlRead(bRequestType, bRequest, wValue, wIndex, wLength,
                    timeout=None):
        return dev.controlRead(bRequestType, bRequest, wValue, wIndex, wLength,
                    timeout=(1000 if timeout is None else timeout))

    def controlWrite(bRequestType, bRequest, wValue, wIndex, data,
                     timeout=None):
        dev.controlWrite(bRequestType, bRequest, wValue, wIndex, data,
                     timeout=(1000 if timeout is None else timeout))

    def interruptRead(endpoint, size, timeout=None):
        return dev.interruptRead(endpoint, size,
                    timeout=(1000 if timeout is None else timeout))

    def interruptWrite(endpoint, data, timeout=None):
        dev.interruptWrite(endpoint, data, timeout=(1000 if timeout is None else timeout))
''',
              file=printer.print_file)

    def header(self):
        indented("#!/usr/bin/env python3")
        comment("Generated by usbrply")
        comment("cmd: %s" % (" ".join(sys.argv), ))
        indented("")

        if self.wrapper:
            self.print_imports()
            print("", file=printer.print_file)
            self.print_wrapper_header()

    def footer(self):
        if not self.wrapper:
            return
        print('''
def open_dev(vid_want, pid_want, usbcontext=None):
    if usbcontext is None:
        usbcontext = usb1.USBContext()
    
    print("Scanning for devices...")
    for udev in usbcontext.getDeviceList(skip_on_error=True):
        vid = udev.getVendorID()
        pid = udev.getProductID()
        if (vid, pid) == (vid_want, pid_want):
            print("Found device")
            print("Bus %03i Device %03i: ID %04x:%04x" % (
                udev.getBusNumber(),
                udev.getDeviceAddress(),
                vid,
                pid))
            return udev.open()
    raise Exception("Failed to find a device")

def main():
    import argparse 

    vid_want = ''' + self.vid_str() + '''
    pid_want = ''' + self.pid_str() + '''
    parser = argparse.ArgumentParser(description="Replay captured USB packets")
    args = parser.parse_args()

    usbcontext = usb1.USBContext()
    dev = open_dev(vid_want, pid_want, usbcontext)
    dev.claimInterface(0)
    dev.resetDevice()
    replay(dev)

if __name__ == "__main__":
    main()
''',
              file=printer.print_file)

    def vid_str(self):
        if self.vid:
            return "0x%04X" % (self.vid, )
        else:
            return "None"

    def pid_str(self):
        if self.pid:
            return "0x%04X" % (self.pid, )
        else:
            return "None"

    def packet_number_str(self, d):
        if self.packet_numbers:
            return "packet %s/%s" % (d["submit"]["packn"],
                                     d["complete"]["packn"])
        else:
            # TODO: consider counting instead of by captured index
            return "packet"

    def parse_data(self, d):
        # print(d)
        if self.sleep and self.prevd and d["type"] != "comment":
            try:
                # Fall back to t_urb for original pcap format on Linux?
                def gett(d):
                    if "t" in d["submit"]:
                        return d["submit"]["t"]
                    elif "t_urb" in d["submit"]:
                        return d["submit"]["t"]
                    else:
                        raise Exception(
                            "Requested sleep but couldn't establish time reference"
                        )

                dt = gett(d) - gett(self.prevd)
            except KeyError:
                raise ValueError("Input JSON does not support timestamps")
            if dt >= 0.001:
                indented("time.sleep(%.3f)" % (dt, ))

        if d["type"] == "comment":
            comment(d["v"])
            return

        packet_numbering = self.packet_number_str(d)

        if "comments" in d:
            for c in d["comments"]:
                comment(c)

        if d["type"] == "controlRead":
            # Is it legal to have a 0 length control in?
            indented("buff = controlRead(0x%02X, 0x%02X, 0x%04X, 0x%04X, %u)" %
                     (d["bRequestType"], d["bRequest"], d["wValue"],
                      d["wIndex"], d["wLength"]))
            indented("validate_read(%s, buff, \"%s\")" % (bytes2AnonArray(
                binascii.unhexlify(d["data"])), packet_numbering))
        elif d["type"] == "controlWrite":
            data_str = bytes2AnonArray(binascii.unhexlify(d["data"]))
            indented("controlWrite(0x%02X, 0x%02X, 0x%04X, 0x%04X, %s)" %
                     (d["bRequestType"], d["bRequest"], d["wValue"],
                      d["wIndex"], data_str))

        elif d["type"] == "bulkRead":
            indented("buff = bulkRead(0x%02X, 0x%04X)" % (d["endp"], d["len"]))
            indented("validate_read(%s, buff, \"%s\")" % (bytes2AnonArray(
                binascii.unhexlify(d["data"])), packet_numbering))
        elif d["type"] == "bulkWrite":
            # Note that its the submit from earlier, not the ack that we care about
            data_str = bytes2AnonArray(binascii.unhexlify(d["data"]))
            # def bulkWrite(self, endpoint, data, timeout=0):
            indented("bulkWrite(0x%02X, %s)" % (d["endp"], data_str))

        elif d["type"] == "interruptIn":
            indented("buff = interruptRead(0x%02X, 0x%04X)" %
                     (d["endp"], d["len"]))
            indented("validate_read(%s, buff, \"%s\")" % (bytes2AnonArray(
                binascii.unhexlify(d["data"])), packet_numbering))

        elif d["type"] == "interruptOut":
            data_str = bytes2AnonArray(binascii.unhexlify(d["data"]))
            indented("interruptWrite(0x%02X, %s)" % (d["endp"], data_str))
        elif d["type"] == "irpInfo":
            comment("IRP_INFO(): func %s" %
                    (d["submit"]["urb"]["usb_func_str"], ))
        elif d["type"] == "abortPipe":
            comment("ABORT_PIPE()")
        else:
            if self.verbose:
                print("LibusbPyPrinter WARNING: dropping %s" % (d["type"], ))

        # these aren't event added to JSON right now
        # print("%s# WARNING: omitting interrupt" % (indent,))

        if d["type"] != "comment":
            self.prevd = d

    def run(self, jgen):
        indent_reset()
        self.header()

        # Last wire command (ie non-comment)
        # Used to optionally generate timing
        self.prevd = None

        # Convert generator into static JSON
        # caches vid/pid among other things
        j = parsers.jgen2j(jgen)

        for d in j["data"]:
            self.parse_data(d)

        if self.wrapper and (self.vid is None or self.pid is None):
            if len(j["device2vidpid"]) != 1:
                raise Exception(
                    "Failed to guess vid/pid: found %u device entries" %
                    len(j["device2vidpid"]))
            for (vid, pid) in j["device2vidpid"].values():
                self.vid = vid
                self.pid = pid
        if self.wrapper:
            self.footer()
