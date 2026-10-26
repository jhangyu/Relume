<h1 align="center">
  <br>
      Frequently Asked Questions
  <br>
</h1>

### Q: I'm facing a playback issue (Video stop loading after start 10-30s). How can I fix it?

The issue might be that your Android OS is preventing MicroG (GMS Core) from running in the background. Follow these instructions to resolve the problem.

[https://dontkillmyapp.com/](https://dontkillmyapp.com/)

---

### Q: How do I use this repository for patching my own app?

Run the Docker stack on your own machine (see "Run locally with Docker (Web UI)" in the [README](../README.md)), open the Web UI, pick an app in the Marketplace, enable it and start a build. Download the patched APK from the build result.

---

### Q: How do I change patches, patch options or the app version?

Patch selection, patch options and version pins live in the web UI ("Relume", `local/`): open an app from the Apps page, then use its settings.

---

### Q: I am facing errors using your apps, what do I do?

I’m so sorry about this, but my repository only patches apps from “scripts” by other devs. I can’t fix errors when apps face errors, so if you get errors, please make an issue in [Revanced](https://github.com/revanced/revanced-patches), [Revanced Extended](https://github.com/inotia00/revanced-patches/tree/revanced-extended) or [Revanced Extended Android 6 & 7](https://github.com/kitadai31/revanced-patches-android6-7/tree/revanced-extended) repositories.

---

### Q: How do I know these apps are safe to use?

You can rest assured that it’s safe because my code is 100% open-source and builds the apps in your own Docker stack. The apk files used for patching are downloaded from the trusted site “APKMirror”.

----
