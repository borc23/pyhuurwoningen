# Step 1: search for rental homes and print them.
import pyhuurwoningen as hw

homes = hw.search("rotterdam", max_price=1500)

print("Found", len(homes), "homes")
for home in homes:
    print(home)
