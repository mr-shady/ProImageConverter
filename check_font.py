import tkinter as tk
from tkinter import font as tkfont

root = tk.Tk()
root.withdraw()

installed = sorted(tkfont.families(root))
matches = [name for name in installed if "vazir" in name.casefold()]

if matches:
    print("Vazirmatn/Vazir font found:")
    for name in matches:
        print("-", name)
else:
    print("Vazirmatn was not found. The app will use Tahoma as fallback.")

root.destroy()
