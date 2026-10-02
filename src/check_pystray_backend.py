import pystray

print("pystray.Icon class:", pystray.Icon)
print("Backend module:", pystray.Icon.__module__)

if "appindicator" in pystray.Icon.__module__:
    print("-> AppIndicator backend (good -- full menu support expected)")
elif "gtk" in pystray.Icon.__module__:
    print("-> Plain GTK backend (GtkStatusIcon -- menu support varies by desktop environment)")
elif "xorg" in pystray.Icon.__module__:
    print("-> Xorg fallback backend (LIMITED menu support -- this is likely the problem)")
else:
    print("-> Unknown backend")