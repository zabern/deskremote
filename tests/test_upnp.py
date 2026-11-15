import httpx
import pytest

from deskremote.raumfeld.upnp import AVT, RC, UpnpDevice, UpnpError

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<serviceList>
<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
<SCPDURL>/rc.xml</SCPDURL><controlURL>/RenderingService/Control</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
<SCPDURL>/avt.xml</SCPDURL><controlURL>TransportService/Control</controlURL></service>
</serviceList></device></root>"""

TRANSPORT_OK = """<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<u:GetTransportInfoResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
<CurrentTransportState>STOPPED</CurrentTransportState><CurrentTransportStatus>OK</CurrentTransportStatus>
<CurrentSpeed>1</CurrentSpeed></u:GetTransportInfoResponse></s:Body></s:Envelope>"""

FAULT = """<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>
<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701</errorCode>
<errorDescription>Transition not available</errorDescription></UPnPError></detail></s:Fault>
</s:Body></s:Envelope>"""


def make(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def desc_or(handler):
    def h(req):
        if req.url.path == "/dev.xml":
            return httpx.Response(200, text=DESC)
        return handler(req)
    return h


def test_load_collects_control_urls():
    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(desc_or(lambda r: httpx.Response(404))))
    assert dev.services[RC] == "http://10.0.0.1:5000/RenderingService/Control"
    assert dev.services[AVT] == "http://10.0.0.1:5000/TransportService/Control"


def test_call_sends_soapaction_and_parses_out_args():
    seen = {}

    def h(req):
        seen["action"] = req.headers["SOAPACTION"]
        seen["body"] = req.content.decode()
        return httpx.Response(200, text=TRANSPORT_OK)

    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(desc_or(h)))
    out = dev.call(AVT, "GetTransportInfo", InstanceID=0)
    assert seen["action"] == '"urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"'
    assert "<InstanceID>0</InstanceID>" in seen["body"]
    assert out["CurrentTransportState"] == "STOPPED"
    assert out["CurrentSpeed"] == "1"


def test_call_escapes_args():
    seen = {}

    def h(req):
        seen["body"] = req.content.decode()
        return httpx.Response(200, text=TRANSPORT_OK)

    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(desc_or(h)))
    dev.call(AVT, "SetAVTransportURI", InstanceID=0, CurrentURI="http://x/?a=1&b=<2>", CurrentURIMetaData="")
    assert "http://x/?a=1&amp;b=&lt;2&gt;" in seen["body"]


def test_soap_fault_raises():
    dev = UpnpDevice.load(
        "http://10.0.0.1:5000/dev.xml", make(desc_or(lambda r: httpx.Response(500, text=FAULT)))
    )
    with pytest.raises(UpnpError, match="701"):
        dev.call(AVT, "Play", InstanceID=0, Speed=1)


def test_unknown_service_raises():
    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(desc_or(lambda r: httpx.Response(404))))
    with pytest.raises(UpnpError):
        dev.call("urn:nope", "X")


def test_connection_error_becomes_upnp_error():
    def h(req):
        raise httpx.ConnectError("weg")

    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(desc_or(h)))
    with pytest.raises(UpnpError):
        dev.call(AVT, "Play", InstanceID=0, Speed=1)


def test_foreign_control_url_is_ignored():
    desc = DESC.replace("TransportService/Control", "http://evil.example/ctrl")
    client = make(lambda r: httpx.Response(200, text=desc))
    dev = UpnpDevice.load("http://10.0.0.1:5000/dev.xml", client)
    assert AVT not in dev.services
    assert RC in dev.services


def test_entity_bomb_in_description_raises_upnp_error():
    bomb = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><root>&b;</root>'
    with pytest.raises(UpnpError):
        UpnpDevice.load("http://10.0.0.1:5000/dev.xml", make(lambda r: httpx.Response(200, text=bomb)))
