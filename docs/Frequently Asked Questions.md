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

You should [fork](https://github.com/FiorenMas/Revanced-And-Revanced-Extended-Non-Root/fork) the repository, and then go to the `Action` tab
![Click Action](../docs/image/FAQ/patching-app-1.png)

Click "I understand my workflows, go ahead and enable them"
![Click "I understand my workflows, go ahead and enable them"](../docs/image/FAQ/patching-app-2.png)

Open the "Build" workflow, click "Run workflow", enter a source id (for example `morphe`) or `all` in `source`, and tick `force` to build even when nothing changed. The workflow also runs every day at 09:00 UTC and builds only the sources whose patches changed.

And then go to Readme click download your patched apps!

---

### Q: How do I change patches, patch options or the app version?

Patch selection, patch options and version pins live in the web UI ("Patchbay", `local/`): open an app, then use the Patches, Options and Version tabs. To make GitHub Actions build with the same settings, export the catalog and commit it:

`docker compose -f local/docker-compose.yml exec app python3 -m webui export-seed > local/webui/catalog-seed.json`

---

### Q: I am facing errors using your apps, what do I do?

I’m so sorry about this, but my repository only patches apps from “scripts” by other devs. I can’t fix errors when apps face errors, so if you get errors, please make an issue in [Revanced](https://github.com/revanced/revanced-patches), [Revanced Extended](https://github.com/inotia00/revanced-patches/tree/revanced-extended) or [Revanced Extended Android 6 & 7](https://github.com/kitadai31/revanced-patches-android6-7/tree/revanced-extended) repositories.

---

### Q: How do I know these apps are safe to use?

You can rest assured that it’s safe because my code is 100% open-source and uses GitHub Actions to patch the apps. The apk files used for patching are downloaded from the trusted site “APKMirror”.

----
