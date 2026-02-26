"""Quick test for _extract_price_per_sqm against the real HTML structure."""
import re
from bs4 import BeautifulSoup

HTML = """
<div class="price">
  <div class="cena">145 000 &#8364;<br>283 595.35 &#1083;&#1074;.</div>
  <br>
  <span>(1 686 &#8364;, 3 297.53 &#1083;&#1074;./m<sup>2</sup>)</span>
  <span>
    <a onclick="showpricechange()" title="history"><img src="x.svg"></a>
  </span>
  <div style="color:#000">&#1053;&#1077; &#1089;&#1077; &#1085;&#1072;&#1095;&#1080;&#1089;&#1083;&#1103;&#1074;&#1072; &#1044;&#1044;&#1057;</div>
  <div class="priceHistory priceHistory2" id="priceHistory2">
    <div class="title">History <span onclick="x()"></span></div>
  </div>
</div>
"""

soup = BeautifulSoup(HTML, "html.parser")
price_div = soup.find("div", class_=lambda x: x and x.startswith("price"))

print("Direct span children:")
for i, span in enumerate(price_div.find_all("span", recursive=False)):
    text = span.get_text(" ", strip=True)
    print(f"  span[{i}]: {repr(text)}")
    if "/m" not in text:
        print("    -> skip (no /m)")
        continue
    m = re.search(r'\(\s*(\d[\d\s.]*(?:,\d+)?)\s*\u20ac', text)
    if m:
        raw = m.group(1).strip().replace(" ", "").replace(",", ".")
        print(f"    -> MATCH: '{raw} \u20ac/m\u00b2'")
    else:
        print(f"    -> regex no match, text was: {repr(text)}")

print("\nDone.")
