import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET
from sunhill_nvr import camera_settings as s,camera_control as c
from sunhill_nvr.baichuan_control import Baichuan,Rejected

class Settings(unittest.TestCase):
    def test_cgi_capabilities_and_preserve_other_fields(self):
        client=Mock()
        def get(cmd):
            if cmd=='GetIsp':return {'value':{'Isp':{'channel':0,'dayNight':'Auto','mirror':0,'other':42}},'range':{'Isp':{'dayNight':['Auto','Color']}}}
            if cmd=='GetImage':return {'value':{'Image':{'bright':20}},'range':{'Image':{'bright':{'min':0,'max':50}}}}
            raise c.Unsupported('no')
        client.get.side_effect=get
        settings,partial=s.read(client);self.assertFalse(partial)
        self.assertEqual({x['key'] for x in settings},{'day_night','mirror','image_brightness'})
        s.apply(client,'day_night','Color');client.set.assert_called_once_with('SetIsp',{'Isp':{'channel':0,'dayNight':'Color','mirror':0,'other':42}})
        with self.assertRaises(c.Unsupported):s.apply(client,'day_night','Black&White')
        with self.assertRaises(c.Unsupported):s.apply(client,'image_brightness',51)
        self.assertFalse(s.valid('mirror',1));self.assertFalse(s.valid('raw',True))
        self.assertFalse(s.valid('image_brightness',True))
    def test_native_settings(self):
        client=Mock(spec=Baichuan)
        def request(cmd,*args,**kwargs):
            if cmd==208:return ET.fromstring('<body><LedState><channelId>0</channelId><state>auto</state><lightState>close</lightState></LedState></body>')
            if cmd==289:return ET.fromstring('<body><FloodlightTask><channel>0</channel><brightness_cur>40</brightness_cur><brightness_min>10</brightness_min><brightness_max>90</brightness_max><schedule><enable>1</enable></schedule></FloodlightTask></body>')
            if cmd==46:return ET.fromstring('<body><MD><channelId>0</channelId><enable>1</enable></MD></body>')
            if cmd==232:return ET.fromstring('<body><AudioTask><channelId>0</channelId><enable>0</enable></AudioTask></body>')
            if cmd in (209,290,47,233):return None
            raise Rejected('no')
        client.request.side_effect=request
        result,partial=s.read(client);self.assertFalse(partial);self.assertEqual({x['key'] for x in result},set(s.NATIVE))
        s.apply(client,'floodlight_brightness',60);cmd,body=client.request.call_args.args;self.assertEqual(cmd,290);self.assertEqual(ET.fromstring(body).findtext('.//brightness_cur'),'60');self.assertEqual(ET.fromstring(body).findtext('.//schedule/enable'),'1')
        s.apply(client,'ir_mode','Off');cmd,body=client.request.call_args.args;self.assertEqual(cmd,209);self.assertEqual(ET.fromstring(body).findtext('.//state'),'close')
        with self.assertRaises(c.Unsupported):s.apply(client,'floodlight_brightness',99)
    def test_wrong_channel_and_missing_range(self):
        self.assertIsNone(s.descriptor('mirror',{'value':{'Isp':{'channel':1,'mirror':0}}}))
        self.assertIsNone(s.descriptor('image_brightness',{'value':{'Image':{'bright':10}}}))
