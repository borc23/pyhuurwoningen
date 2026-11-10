# Step 4: do your own analysis with plain Python.
import pyhuurwoningen as hw

homes = hw.search("groningen", pages=2)

# Keep only homes that show both a price and a size.
known = [h for h in homes if h.price and h.area]

# Sort by price per square metre, cheapest first.
known.sort(key=lambda h: h.price / h.area)

for home in known[:5]:
    print(f"€{home.price / home.area:.2f} per m²  ->  {home}")

average = sum(h.price for h in known) / len(known)
print(f"\nAverage rent: €{average:.0f} per month")
