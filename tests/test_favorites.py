from pathlib import Path

import pytest

from deskremote.raumfeld.favorites import (
    FAVORITES, TUNEIN_SEARCH, AnlageFavorite, FavoriteError, add_favorite, find_by_ref,
    list_favorites, parse_favorites, play_uri, playsingle_uri, remove_favorite, search_stations,
)
from deskremote.raumfeld.media import CD, PAGE_SIZE, MediaServer

FIX = Path(__file__).parent / "fixtures"
FAV_XML = (FIX / "favorites_my.xml").read_text(encoding="utf-8")
MS_UDN = "uuid:c24a80f6-f43a-51a1-9031-97cedeb3c9f3"


class FakeDevice:
    def __init__(self, didl=FAV_XML, returned=None, new_id="0/Favorites/MyFavorites/2000"):
        self.calls = []
        self.didl = didl
        self.returned = returned
        self.new_id = new_id

    def call(self, service, action, **args):
        self.calls.append((service, action, args))
        if action == "Browse" and args.get("BrowseFlag") == "BrowseMetadata":
            return {"Result": "<DIDL-Lite/>", "NumberReturned": "1", "TotalMatches": "1"}
        if action == "CreateReference":
            return {"NewID": self.new_id}
        if action == "DestroyObject":
            return {}
        n = self.returned if self.returned is not None else self.didl.count("parentID=")
        return {"Result": self.didl, "NumberReturned": str(n), "TotalMatches": "0"}

    def actions(self):
        return [a for _s, a, _ in self.calls]


def server(dev):
    return MediaServer(MS_UDN, "Raumfeld MediaServer", dev, raumfeld=True)


def test_parse_real_favorites():
    favs = parse_favorites(FAV_XML)
    assert [f.kind for f in favs] == ["station", "station", "station", "album", "artist"]
    fm4 = next(f for f in favs if f.id == "0/Favorites/MyFavorites/315")
    assert fm4.title == "ORF Radio FM4"
    assert fm4.ref_id == "0/RadioTime/Search/s-s8235"
    assert not fm4.container and fm4.res is None
    assert fm4.art_url.startswith("https://cdn-profiles.tunein.com/s8235/")
    oe1 = next(f for f in favs if f.id.endswith("/314"))
    assert oe1.title == "ORF Radio Österreich 1"
    album = favs[3]
    assert album.container and album.artist == "Michael Jackson" and album.title == "Thriller"
    assert album.ref_id == "0/My Music/Artists/Michael%20Jackson/Michael%20Jackson+Thriller"


def test_parse_empty_and_foreign_art():
    assert parse_favorites("") == []
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0/Favorites/MyFavorites/1" refID="0/x"><dc:title>X</dc:title>'
            '<upnp:class>object.item.audioItem.audioBroadcast.radio</upnp:class>'
            '<upnp:albumArtURI>file:///etc/passwd</upnp:albumArtURI></item></DIDL-Lite>')
    assert parse_favorites(didl)[0].art_url is None


def test_list_favorites_single_page():
    dev = FakeDevice()
    favs = list_favorites(server(dev))
    assert len(favs) == 5
    assert dev.calls[0][2]["ObjectID"] == FAVORITES
    assert dev.actions() == ["Browse"]


def test_list_favorites_pages_by_number_returned():
    items = "".join(
        f'<item parentID="{FAVORITES}" id="{FAVORITES}/{i}" refID="0/RadioTime/Search/s-s{i}">'
        f'<dc:title>S{i}</dc:title><upnp:class>object.item.audioItem.audioBroadcast.radio</upnp:class></item>'
        for i in range(PAGE_SIZE))
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            + items + "</DIDL-Lite>")

    class Paged(FakeDevice):
        def call(self, service, action, **args):
            self.calls.append((service, action, args))
            if args["StartingIndex"] == 0:
                return {"Result": didl, "NumberReturned": str(PAGE_SIZE), "TotalMatches": "0"}
            return {"Result": FAV_XML, "NumberReturned": "5", "TotalMatches": "0"}

    dev = Paged()
    assert len(list_favorites(server(dev))) == PAGE_SIZE + 5
    assert [c[2]["StartingIndex"] for c in dev.calls] == [0, PAGE_SIZE]


def test_play_uri_station_is_playsingle_like_teufel_app():
    fm4 = parse_favorites(FAV_XML)[0]
    uri = play_uri(MS_UDN, fm4)
    assert uri.startswith("dlna-playsingle://uuid%3Ac24a80f6")
    assert "sid=urn%3Aupnp-org%3AserviceId%3AContentDirectory" in uri
    assert uri.endswith("&iid=" + "0%2FFavorites%2FMyFavorites%2F" + fm4.id.rsplit("/", 1)[1])


def test_play_uri_album_uses_reference_artist_uses_res():
    favs = parse_favorites(FAV_XML)
    album, artist = favs[3], favs[4]
    assert play_uri(MS_UDN, album).startswith("dlna-playcontainer://")
    assert "cid=0%2FMy%20Music%2FArtists%2FMichael%2520Jackson" in play_uri(MS_UDN, album)
    assert play_uri(MS_UDN, artist) == artist.res


def test_playsingle_uri_quotes():
    assert playsingle_uri("uuid:a", "0/x y") == (
        "dlna-playsingle://uuid%3Aa?sid=urn%3Aupnp-org%3AserviceId%3AContentDirectory&iid=0%2Fx%20y")


def test_add_creates_reference_in_my_favorites():
    dev = FakeDevice()
    new = add_favorite(server(dev), "0/RadioTime/Search/s-s16660")
    assert new == "0/Favorites/MyFavorites/2000"
    assert dev.calls[-1] == (CD, "CreateReference",
                             {"ContainerID": FAVORITES, "ObjectID": "0/RadioTime/Search/s-s16660"})


def test_add_is_idempotent():
    dev = FakeDevice()
    assert add_favorite(server(dev), "0/RadioTime/Search/s-s8235") == "0/Favorites/MyFavorites/315"
    assert "CreateReference" not in dev.actions()


@pytest.mark.parametrize("target", ["0/Favorites/MyFavorites/315", "0/Favorites/RecentlyPlayed/26",
                                    "http://evil/x", "", "1/x"])
def test_add_refuses_bad_targets(target):
    dev = FakeDevice()
    with pytest.raises(FavoriteError):
        add_favorite(server(dev), target)
    assert "CreateReference" not in dev.actions()


def test_add_rejects_unexpected_new_id():
    dev = FakeDevice(new_id="0/My Music/Albums/x")
    with pytest.raises(FavoriteError):
        add_favorite(server(dev), "0/RadioTime/Search/s-s1")


def test_remove_only_my_favorites_entries():
    dev = FakeDevice()
    remove_favorite(server(dev), "0/Favorites/MyFavorites/315")
    assert dev.calls[-1] == (CD, "DestroyObject", {"ObjectID": "0/Favorites/MyFavorites/315"})


@pytest.mark.parametrize("bad", [
    "0/My Music/Albums/x", "0/RadioTime/Search/s-s8235", "0/Favorites/MyFavorites",
    "0/Favorites/MyFavorites/", "0/Favorites/MyFavorites/1/2", "0/Favorites/RecentlyPlayed/26",
    "0/Favorites/MyFavorites/../../My Music",
])
def test_remove_refuses_everything_else(bad):
    dev = FakeDevice()
    with pytest.raises(FavoriteError):
        remove_favorite(server(dev), bad)
    assert dev.calls == []


def test_find_by_ref():
    favs = parse_favorites(FAV_XML)
    assert find_by_ref(favs, "0/RadioTime/Search/s-s13606").title == "Radio Paradise"
    assert find_by_ref(favs, "0/RadioTime/Search/s-s1") is None


def test_search_stations_keeps_only_stations():
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
            '<item id="0/RadioTime/Search/s-s8235" parentID="0/RadioTime/Search"><dc:title>ORF Radio FM4</dc:title>'
            '<upnp:class>object.item.audioItem.audioBroadcast.radio</upnp:class></item>'
            '<container id="0/RadioTime/Search/pc-p1" parentID="0/RadioTime/Search"><dc:title>FM4 Podcast</dc:title>'
            '<upnp:class>object.container.trackContainer</upnp:class></container></DIDL-Lite>')
    dev = FakeDevice(didl=didl)
    hits = search_stations(server(dev), 'FM"4')
    assert [h.title for h in hits] == ["ORF Radio FM4"]
    args = dev.calls[0][2]
    assert dev.calls[0][1] == "Search" and args["ContainerID"] == TUNEIN_SEARCH
    assert args["SearchCriteria"] == 'dc:title contains "FM\\"4"'


def test_dataclass_is_hashable():
    assert len({AnlageFavorite("a", None, "t", "station", False)}) == 1
