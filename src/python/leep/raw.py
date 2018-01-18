
from __future__ import print_function

import logging
_log = logging.getLogger(__name__)

import sys, os, socket, random, zlib, json, time

from .base import DeviceBase, AcquireBase, IGNORE, WARN, ERROR

import numpy

be32 = numpy.dtype('>u4')
be16 = numpy.dtype('>u2')

class LEEPDevice(DeviceBase):
    backend = 'leep'

    def __init__(self, addr, timeout=0.1, **kws):
        DeviceBase.__init__(self, **kws)
        host, _sep, port = addr.partition(':')
        self.dest = (host, int(port or '50006'))

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
        self.sock.settimeout(timeout)

        self._readrom()

    def reg_write(self, ops, instance=[]):
        addrs, values = [], []
        for name, value in ops:
            if instance is not None:
                name = self.expand_regname(name, instance=instance)
            info = self.get_reg_info(name, instance=None)
            L = 2**info.get('addr_width', 0)

            if L > 1:
                assert len(value)==L, ('must write whole register', len(value), L)
                # array register
                for A, V in enumerate(value, info['base_addr']):
                    addrs.append(A)
                    values.append(V)
            else:
                addrs.append(info['base_addr'])
                values.append(value)

        addrs  = numpy.asarray(addrs)
        values = numpy.asarray(values)

        self.exchange(addrs,values)

    def reg_read(self, names, instance=[]):
        addrs = []
        lens = []
        for name in names:
            if instance is not None:
                name = self.expand_regname(name, instance=instance)
            info = self.get_reg_info(name, instance=None)
            L = 2**info.get('addr_width', 0)

            lens.append((info, L))
            addrs.extend(range(info['base_addr'], info['base_addr']+L))

        raw = self.exchange(addrs)

        ret = []
        for info, L in lens:
            data, raw = raw[:L], raw[L:]
            assert len(data)==L, (len(data), L)
            if info.get('sign', 'unsigned')=='signed':
                # sign extend
                # mask of data bits excluding sign bit
                mask = (2**(info['data_width']-1))-1
                # invert to give mask of sign bit and extension bits
                mask ^= 0xffffffff
                # test sign bit
                neg = (data & mask)!=0
                # extend only negative numbers
                data[neg] |= mask
                # cast to signed
                data = data.astype('i4')
            ret.append(data)

        return ret

    def set_channel_mask(self, chans=[], instance=[]):
        """Enabled specified channels.
        """
        # list of channel numbers to mask
        chans = reduce(lambda l,r: l|r, [2**(12-n) for n in chans], 0)

        self.reg_write([('chan_keep', chans)], instance=instance)

    def wait_for_acq(self, tag=False, timeout=5.0, instance=[]):
        """Wait for next waveform acquisition to complete.
        If tag=True, then wait for the next acquisition which includes the
        side-effects of all preceding register writes
        """
        start = time.time()

        if tag:
            T, = (self.reg_read(['dsp_tag'], instance=instance)+1)&0xffff
            self.reg_write([('dsp_tag', T)], instance=instance)
            _log.debug('Set Tag %d', T)

        I = self.instance + instance
        # assume that the shell_#_ number is the first
        mask = 2**int(I[0])

        while True:
            self.reg_write([('circle_buf_flip', mask)], instance=None)

            while True:
                now = time.time()
                if now-start >= timeout:
                    raise RuntimeError('Timeout')

                # TODO: use exchange() and optimize to fetch slow_data[33] as well
                ready, = self.reg_read(['llrf_circle_ready'], instance=None)

                if ready&mask:
                    break

            if not tag:
                break

            slow, = self.reg_read(['slow_data'], instance=instance)
            dT = (slow[33] - T) & 0xffff
            if dT >= 0:
                if dT>0:
                    _log.warn('acquisition collides with another client')
                break # all done
            # retry

    def get_channels(self, chans=[], instance=[]):
        """:returns: a list of :py:class:`numpy.ndarray` with the numbered channels.
        chans may be a bit mask or a list of channel numbers
        """
        interested = reduce(lambda l,r: l|r, [2**(12-n) for n in chans], 0)

        keep, data = self.reg_read([
            'chan_keep',
            'circle_data',
        ], instance=instance)

        if (keep & interested) != chans:
            # chans must be a strict sub-set of keep
            raise RuntimeError('Requested channels (%x) not kept (%x)'%(chans, keep))

        # count number of bits set
        nbits, M = 0, mask
        while M!=0:
            if M&1:
                nbits += 1
            M >>= 1

        cdata = {}
        M = 0
        for ch in range(12):
            cmask = 2**(12-ch)
            if not (keep & cmask):
                continue
            if interested & cmake:
                cdata[ch] = data[M:nbits:]

            M += 1

        # finally, ensure the results are in the same order as args
        return list([cdata[ch] for ch in chans])

    def _exchange(self, addrs, values=None):
        """Exchange a single low level message
        """
        pad = None
        if len(addrs)<3:
            pad = 3-len(addrs)
            addrs.extend([0]*pad)
            values.extend([None]*pad)

        msg = numpy.zeros(2+2*len(addrs), dtype=be32)
        msg[0] = random.randint(0,0xffffffff)
        msg[1] = msg[0]^0xffffffff

        for i,(A, V) in enumerate(zip(addrs, values), 1):
            A &= 0x00ffffff
            if V is None:
                A |= 0x10000000
            msg[2*i] = A
            msg[2*i+1] = V or 0

        tosend = msg.tostring()
        _log.debug("%s Send (%d) %s", self.dest, len(tosend), repr(tosend))
        self.sock.sendto(tosend, self.dest)

        while True:
            reply, src = self.sock.recvfrom(1024)
            _log.debug("%s Recv (%d) %s", src, len(reply), repr(reply))

            if len(reply)%8:
                reply = reply[:-(len(reply)%8)]

            if len(tosend)!=len(reply):
                _log.error("Reply truncated %d %d", len(tosend), len(reply))
                continue

            reply = numpy.fromstring(reply, be32)
            if (msg[:2]!=reply[:2]).any():
                _log.error('Ignore reply w/o matching nonce %s %s', msg[:2], reply[:2])
                continue
            elif (msg[2::2]!=reply[2::2]).any():
                _log.error('reply addresses are out of order')
                continue

            break

        ret = reply[3::2]
        if pad:
            ret = ret[:-pad]
        return ret

    def exchange(self, addrs, values=None):
        """Accepts a list of address and values (None to read).
        Returns a numpy.ndarray in the same order.
        """
        addrs = list(addrs)

        if values is None:
            values = [None]*len(addrs)
        else:
            values = list(values)

        ret = numpy.zeros(len(addrs), be32)
        for i in range(0, len(addrs), 127):
            A, B = addrs[i:i+127], values[i:i+127]

            P = self._exchange(A, B)
            ret[i:i+127] = P

        return ret

    def _readrom(self):
        self.descript = None
        self.codehash = None
        self.jsonhash = None
        self.regmap = None

        values = self.exchange(range(0x800, 0x1000))

        values = numpy.frombuffer(values, be16)
        _log.debug("ROM[0] %08x", values[0])
        values = values[1::2] # discard upper bytes

        while len(values):
            type = values[0]>>14
            size = values[0]&0x3fff
            _log.debug("ROM Descriptor type=%d size=%d", type, size)

            if type==0:
                break

            blob, values = values[1:size+1], values[size+1:]
            if len(blob)!=size:
                raise ValueError("Truncated ROM Descriptor")

            if type==1:
                blob = blob.tostring()
                if self.descript is None:
                    self.descript = blob
                else:
                    _log.info("Extra ROM Text '%s'", blob)
            elif type==2:
                blob = ''.join(["%04x"%b for b in blob])
                if self.jsonhash is None:
                    self.jsonhash = blob
                elif self.codehash is None:
                    self.codehash = blob
                else:
                    _log.info("Extra ROM Hash %s", blob)

            elif type==3:
                if self.regmap is not None:
                    _log.error("Ignoring additional JSON blob in ROM")
                else:
                    self.regmap = json.loads(zlib.decompress(blob.tostring()).decode('ascii'))

        if self.regmap is None:
            raise RuntimeError('ROM contains no JSON')
