import os
import smtplib
from email.message import EmailMessage

import pandas as pd

PAGE_BG = "#4A5759"  # outer background (slate)
CARD_BG = "#F7E1D7"  # listing card (peach)
INK = "#4A5759"  # primary text on light cards (slate)
ACCENT = "#EDAFB8"  # borders / section underline (pink)
BADGE_BG = "#B0C4B1"  # "available" pill (sage)
ON_DARK = "#F7E1D7"  # headings sitting on the slate page (peach)
MUTED_LIGHT = "#DEDBD2"  # secondary text on the slate page (sand)
SAGE = "#B0C4B1"  # counts / footer accents

NEIGHBORHOOD_NAMES = {
    "south-end-boston-ma": "South End, Boston",
    "back-bay-boston-ma": "Back Bay, Boston",
    "allston-ma": "Allston",
    "cambridge-ma": "Cambridge",
    "somerville-ma": "Somerville",
}

# official MBTA line colors
LINE_COLORS = {
    "Red": "#DA291C",
    "Orange": "#ED8B00",
    "Blue": "#003DA5",
    "Green": "#00843D",
    "Silver": "#7C878E",
}

def _safe(val, default=""):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return val

def _apartment_card(apt):
    line = str(_safe(apt.get("tstop_line")))
    dot = LINE_COLORS.get(line, "#888888")
    url = _safe(apt.get("URL"), "#")
    addr = _safe(apt.get("Address"), "Unknown address")
    price = _safe(apt.get("Price"))
    avail = _safe(apt.get("Available_Raw"))
    tstop = _safe(apt.get("closest_tstop_address"))
    tstopname = _safe(apt.get('tstop_name'))
    score = apt.get("ranking")
    miles = apt.get("driving_distance_miles")

    score_html = f"{score:.2f}" if pd.notna(score) else "&mdash;"
    miles_html = f"{miles:.1f} mi to {tstopname}" if pd.notna(miles) else ""
    avail_pill = (f'<span style="display:inline-block;background:{BADGE_BG};color:{INK};'
                  f'font-size:11px;font-weight:bold;padding:3px 8px;border-radius:10px;">'
                  f'{avail}</span>') if avail else ""

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border-collapse:collapse;margin:0 0 12px 0;background:{CARD_BG};
                  border:1px solid {ACCENT};border-radius:8px;">
      <tr><td style="padding:16px;font-family:Arial,Helvetica,sans-serif;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="vertical-align:top;">
              <a href="{url}"
                 style="color:{INK};font-size:16px;font-weight:bold;text-decoration:none;">
                 {addr}</a>
              <div style="color:{INK};opacity:0.75;font-size:13px;padding-top:7px;line-height:1.4;">
                <span style="display:inline-block;height:9px;width:9px;border-radius:50%;
                             background:{dot};"></span>
                {line} Line &middot; {tstop} &middot; {miles_html}
              </div>
            </td>
            <td style="vertical-align:top;text-align:right;white-space:nowrap;padding-left:14px;">
              <div style="font-size:18px;font-weight:bold;color:{INK};">{price}</div>
              <div style="padding-top:6px;">{avail_pill}</div>
              <div style="font-size:11px;color:{INK};opacity:0.6;padding-top:6px;">score {score_html}</div>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>"""

def build_html(df, neighborhood_col="neighborhood", order=None, subtitle=""):
    if order is None:
        order = list(pd.unique(df[neighborhood_col].dropna()))

    sections = []
    for key in order:
        group = df[df[neighborhood_col] == key]
        if group.empty:
            continue
        group = group.sort_values("ranking", ascending=False)
        title = NEIGHBORHOOD_NAMES.get(key, str(key))
        cards = "".join(_apartment_card(apt) for _, apt in group.iterrows())
        sections.append(f"""
          <tr><td style="padding:26px 0 10px 0;font-family:Arial,Helvetica,sans-serif;">
            <div style="font-size:17px;font-weight:bold;color:{ON_DARK};
                        border-bottom:2px solid {ACCENT};padding-bottom:6px;">
              {title}
              <span style="font-weight:normal;color:{SAGE};font-size:14px;">
                &nbsp;{len(group)} listing{'s' if len(group) != 1 else ''}</span>
            </div>
          </td></tr>
          <tr><td>{cards}</td></tr>""")

    sub = (f'<div style="color:{MUTED_LIGHT};font-size:13px;padding-top:6px;">{subtitle}</div>'
           if subtitle else "")

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:{PAGE_BG};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:{PAGE_BG};">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="max-width:600px;width:100%;">
        <tr><td style="font-family:Arial,Helvetica,sans-serif;padding-bottom:4px;">
          <div style="font-size:22px;font-weight:bold;color:{ON_DARK};">Apartment Hunt Digest</div>
          {sub}
        </td></tr>
        {''.join(sections)}
        <tr><td style="font-family:Arial,Helvetica,sans-serif;color:{SAGE};
                       font-size:11px;padding-top:28px;text-align:center;">
          Generated automatically &middot; addresses link to apartments.com
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def build_plaintext(df, neighborhood_col="neighborhood", order=None):
    if order is None:
        order = list(pd.unique(df[neighborhood_col].dropna()))
    lines = ["APARTMENT HUNT DIGEST", ""]
    for key in order:
        group = df[df[neighborhood_col] == key]
        if group.empty:
            continue
        group = group.sort_values("ranking", ascending=False)
        lines.append(NEIGHBORHOOD_NAMES.get(key, str(key)).upper())
        for _, a in group.iterrows():
            lines.append(f"  {_safe(a.get('Price'))}  {_safe(a.get('Address'))}")
            lines.append(f"    {_safe(a.get('URL'))}")
        lines.append("")
    return "\n".join(lines)

