import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from sunhill_nvr import camera_control as c


def response(key,value,limits=None):
    row={'value':{key:value}}
    if limits is not None:row['range']={key:limits}
    return row


class ControlsTests(unittest.TestCase):
    def fixture(self):
        client=Mock()
        rows={
            'GetWhiteLed':response('WhiteLed',{'state':0,'bright':70}),
            'GetIrLights':response('IrLights',{'state':'Auto'},{'state':['Auto','Off']}),
            'GetZoomFocus':response('ZoomFocus',{'zoom':{'pos':3},'focus':{'pos':42}},{'zoom':{'pos':{'min':0,'max':28}},'focus':{'pos':{'min':0,'max':238}}}),
            'GetAutoFocus':response('AutoFocus',{'disable':0}),
            'GetPtzPreset':response('PtzPreset',[{'id':2,'enable':1,'name':'Brama'},{'id':3,'enable':0,'name':'Disabled'}])}
        client.get.side_effect=lambda name:rows[name]
        return client

    def test_private_ip_and_encoded_credentials(self):
        client=c.Reolink('rtsp://admin:p%40ss%3Aword@192.168.1.30/h264Preview_01_main')
        self.assertEqual((client.ip,client.username,client.password),('192.168.1.30','admin','p@ss:word'))
        for url in ('rtsp://u:p@127.0.0.1/a','rtsp://u:p@8.8.8.8/a','rtsp://u:p@camera.local/a','rtsp://192.168.1.30/a'):
            with self.assertRaises(c.ControlError):c.Reolink(url)

    def test_capabilities_and_bounds(self):
        result=c.capabilities(self.fixture())
        self.assertEqual(result['controls'],{'light':False,'brightness':70,'ir':{'value':'Auto','options':['Auto','Off']},'zoom':{'value':3,'min':0,'max':28},'focus':{'value':42,'min':0,'max':238},'autofocus':True,'presets':[{'id':2,'name':'Brama'}]})
        self.assertFalse(result['partial'])

    def test_unsupported_no_controls_and_partial(self):
        client=Mock();client.get.side_effect=c.Unsupported('no')
        self.assertEqual(c.capabilities(client),{'controls':{},'partial':False})
        client.get.side_effect=c.ControlError('timeout')
        self.assertEqual(c.capabilities(client),{'controls':{},'partial':True})

    def test_set_commands(self):
        client=self.fixture()
        for control,value,cmd,payload in (
            ('light',True,'SetWhiteLed',{'WhiteLed':{'channel':0,'state':1}}),
            ('brightness',50,'SetWhiteLed',{'WhiteLed':{'channel':0,'bright':50}}),
            ('ir','Off','SetIrLights',{'IrLights':{'channel':0,'state':'Off'}}),
            ('zoom',28,'StartZoomFocus',{'ZoomFocus':{'channel':0,'op':'ZoomPos','pos':28}}),
            ('focus',42,'StartZoomFocus',{'ZoomFocus':{'channel':0,'op':'FocusPos','pos':42}}),
            ('autofocus',False,'SetAutoFocus',{'AutoFocus':{'channel':0,'disable':1}}),
            ('preset',2,'PtzCtrl',{'channel':0,'op':'ToPos','id':2})):
            c.apply(client,*c.validate_command({'control':control,'value':value}));client.set.assert_called_with(cmd,payload)

    def test_invalid_controls_never_write(self):
        client=self.fixture()
        for control,value in [('zoom',29),('focus',239),('ir','On'),('preset',3)]:
            with self.assertRaises(c.ControlError):c.apply(client,control,value)
        for data in [{'control':'Reboot','value':1},{'control':'zoom','value':True},{'control':'light','value':1},{'control':'brightness','value':101},{'control':'light','value':True,'ip':'10.0.0.1'}]:
            with self.assertRaises(c.ControlError):c.validate_command(data)
        client.set.assert_not_called()

    def test_missing_range_not_guessed(self):
        self.assertIsNone(c.position(response('ZoomFocus',{'zoom':{'pos':3}}),'zoom'))

    def test_transport_no_redirect_no_leaks(self):
        client=c.Reolink('rtsp://u:secret@192.168.1.30/a')
        with patch('sunhill_nvr.camera_control.http.client.HTTPConnection') as factory:
            conn=factory.return_value;conn.getresponse.return_value.status=302
            with self.assertRaises(c.ControlError) as cm:client._call('Login',{'User':{'password':'secret'}})
            self.assertNotIn('secret',str(cm.exception));self.assertEqual(factory.call_count,1);conn.close.assert_called_once()

    def test_onvif_identity_without_control(self):
        import xml.etree.ElementTree as ET
        xml=ET.fromstring('<Envelope><Body><GetDeviceInformationResponse><Manufacturer>Reolink</Manufacturer><Model>Lumus</Model><FirmwareVersion>v3</FirmwareVersion><HardwareId>IPC</HardwareId></GetDeviceInformationResponse></Body></Envelope>')
        with patch.object(c,'soap',return_value=xml) as soap:
            result=c.supplement_identity({'controls':{},'partial':True},'rtsp://user:secret@192.168.1.30/main')
            self.assertEqual(result['device']['model'],'Lumus');self.assertEqual(result['device']['identity_source'],'ONVIF');self.assertEqual(result['controls'],{})
            self.assertEqual(soap.call_args.args[0],'http://192.168.1.30:8000/onvif/device_service')
            self.assertNotIn('secret',str(result))
    def test_identity_never_sends_credentials_to_other_host(self):
        with patch.object(c,'soap',side_effect=OSError) as soap:
            self.assertEqual(c.identify_onvif('rtsp://u:p@192.168.1.30/main','http://192.168.1.99/onvif/device_service','other','secret','digest'),{})
            self.assertTrue(soap.call_args_list)
            self.assertTrue(all('192.168.1.30' in call.args[0] and call.args[3]=='p' for call in soap.call_args_list))
            soap.reset_mock();self.assertEqual(c.identify_onvif('rtsp://u:p@8.8.8.8/main'),{});soap.assert_not_called()
    def test_identity_preserves_controls(self):
        result={'controls':{'light':True},'device':{'model':'Lumus','protocol':'CGI HTTP'}}
        with patch.object(c,'identify_onvif') as onvif:
            self.assertEqual(c.supplement_identity(result,'rtsp://u:p@192.168.1.30/main'),result);onvif.assert_not_called()
    def test_login_logout_and_multichannel_rejected(self):
        client=c.Reolink('rtsp://u:p@192.168.1.30/a')
        with patch.object(client,'_call',side_effect=[response('Token',{'name':'abc'}),response('DevInfo',{'channelNum':4}),{'value':{}}]) as call:
            with self.assertRaises(c.ControlError):
                with client:pass
            self.assertEqual(call.call_args_list[-1].args[0],'Logout')

if __name__=='__main__':unittest.main()
