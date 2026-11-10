# Step 3: save homes to a CSV file and open it in Excel or Google Sheets.
import pyhuurwoningen as hw

homes = hw.search("amsterdam", pages=2, min_rooms=2)
hw.save_csv(homes, "amsterdam.csv")
print("Saved", len(homes), "homes to amsterdam.csv")
