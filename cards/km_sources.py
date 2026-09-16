"""
Jackson Purchase station registry for the Weather Brother calm card.

Design rule: this module is the single source of truth for what a site id
means. Nothing downstream re-derives a county name, a zone code, or a
coordinate. Fields that have not been confirmed against a primary source are
None with verified=False, and touching one in a production run raises.

That is the guard against the failure mode where the right constants get
applied to the wrong source.

To fill in the unverified fields: open https://www.kymesonet.org/data/camera
and click each Purchase county. The four-character site id appears in the URL
(.../data/camera/PRYB). The station page lists the coordinates.
"""

from dataclasses import dataclass, field


class UnverifiedField(RuntimeError):
    """Raised when a production run reads a field nobody has confirmed."""


@dataclass
class Station:
    key: str                      # our stable slug, never changes
    county: str                   # verified via api.weather.gov
    zone: str                     # verified via api.weather.gov
    site_id: str | None = None    # Kentucky Mesonet 4-char id
    place: str | None = None      # human label, e.g. "6 SW Mayfield"
    lat: float | None = None
    lon: float | None = None
    has_camera: bool | None = None
    unverified: set[str] = field(default_factory=set)

    def require(self, name: str, allow_unverified: bool = False):
        value = getattr(self, name)
        if name in self.unverified and not allow_unverified:
            raise UnverifiedField(
                f"{self.key}.{name} has not been confirmed against a primary "
                f"source. Confirm it, clear it from .unverified, or pass "
                f"--allow-unverified for fixture work."
            )
        return value


def _s(key, county, zone, **known):
    """Build a Station, marking every field not passed in as unverified."""
    optional = {"site_id", "place", "lat", "lon", "has_camera"}
    unverified = optional - set(known)
    return Station(key=key, county=county, zone=zone, unverified=unverified, **known)


# County names and zone codes below were confirmed live against
# api.weather.gov/zones/county/<zone> on 2026-09-15. Do not edit without
# re-running that check.
#
# Graves site id PRYB confirmed via NCEI storm report for the 2023-12-11
# western Kentucky precipitation event (station 6 SW Mayfield).
#
# The other four site ids were confirmed 2026-09-16 from the station selector
# on kymesonet.org, which carries the network's own label-to-id list for all 91
# stations, cross-checked three ways: each /data/station/<ID> page renders its
# own county name, the ids match the stills already sitting in
# Nadocast\Purchase Mesonet Cams, and each id agrees with the place already
# recorded below.
#
# HCKM is FULTON county, not Hickman county. Hickman is the county seat of
# Fulton County and the station sits at the high school there. Kentucky also
# has a Hickman County, and it has no Mesonet station. Do not "correct" this.
#
# The fifth Purchase site is Marshall (DRFN). McCracken, Carlisle and Hickman
# counties appear nowhere in the selector's 91-station list, so there is no
# station to read in any of them, and the three placeholder candidates that
# used to sit here were removed. POST_ORDER carries marshall in the slot
# mccracken held; reorder if the carousel should run differently.
#
# place is the county name on all five, set 2026-09-16, so the second line of
# every card face reads the same shape. wb_card renders this field as
# "Station <ID>  .  <place>, KY", and its docstring example is a bare town
# ("Bandana"), so these read "Graves, KY" rather than "Mayfield, KY".
#
# The station descriptors that used to sit here are recorded, not deleted, in
# case the face ever wants the specific site back:
#   ballard  BAND  "5 NE La Center (Bandana)"
#   fulton   HCKM  "Hickman (Fulton County High School)"
#   graves   PRYB  "6 SW Mayfield"  -- NCEI, 2023-12-11 storm report

STATIONS: dict[str, Station] = {
    s.key: s
    for s in [
        _s("ballard", "Ballard", "KYC007", site_id="BAND",
           place="Ballard", has_camera=True),
        _s("calloway", "Calloway", "KYC035", site_id="MRRY",
           place="Calloway"),
        _s("fulton", "Fulton", "KYC075", site_id="HCKM",
           place="Fulton"),
        _s("graves", "Graves", "KYC083", site_id="PRYB",
           place="Graves"),
        _s("marshall", "Marshall", "KYC157", site_id="DRFN",
           place="Marshall"),
    ]
}

# Post order for the carousel. Filenames are numbered from this list, so the
# five cards sort into the order you want them to appear.
POST_ORDER = ["ballard", "marshall", "graves", "calloway", "fulton"]


def active_stations(allow_unverified: bool = False) -> list[Station]:
    """The five stations in post order, validated."""
    out = []
    for key in POST_ORDER:
        st = STATIONS[key]
        st.require("site_id", allow_unverified)
        out.append(st)
    return out


if __name__ == "__main__":
    for key, st in STATIONS.items():
        missing = ", ".join(sorted(st.unverified)) or "none"
        print(f"{key:10s} {st.zone}  site_id={st.site_id or '-':6s} unverified: {missing}")
