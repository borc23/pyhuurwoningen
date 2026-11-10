# Step 2: look at one home more closely.
import pyhuurwoningen as hw

homes = hw.search("utrecht")
first = homes[0]

details = hw.get_details(first)
print(details.title)
print("Rent:", details.price, "euro per month")
print("Size:", details.area, "m²")
print("Bedrooms:", details.bedrooms)
print("Energy label:", details.energy_label)
print()
print(details.description)
