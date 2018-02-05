
import unittest

from ..base import DeviceBase

class TestingDevice(DeviceBase):
    def __init__(self, *args, **kws):
        DeviceBase.__init__(self, *args, **kws)
        self.regmap = {
            'XXX': {
                'base_addr':0x10100,
                'addr_width':4*4, # 4 instructions
                'data_width':16,
            },
            'test1': {
                'base_addr':0x20200,
                'addr_width':0,
                'data_width':32,
            },
            'test2': {
                'base_addr':0x30300,
                'addr_width':1,
                'data_width':32,
            },
        }

class TestXXX(unittest.TestCase):
    def test_assemble(self):
        D = TestingDevice()
        prog = D.assemble_tgen([
            ('set', 'test1', 0x12345678),
            ('sleep', 0xabcd),
            ('set', 'test2[0]', 0x01020304),
            ('set', 'test2[1]', 0x05060708),
        ])

        prog, zeros = prog[:12], prog[12:]
        self.assertListEqual(zeros, [0]*len(zeros))
        self.assertListEqual(prog, [
            0xabcd, 0x20200, 0x1234, 0x5678,
            0,      0x30300, 0x0102, 0x0304,
            0,      0x30301, 0x0506, 0x0708,
        ])
