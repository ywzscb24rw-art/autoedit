# AutoEdit beta: getting started

Thanks for testing AutoEdit! It records your screen (or takes a video you already have), transcribes it, and edits it for you: it cuts filler words, dead air, and retakes, and it can find short clips for TikTok, Reels, and Shorts.

**You'll need:** a Mac with an Apple chip (M1 or newer) running macOS 14 or later, about 3 GB of free space, and around 10 minutes.

---

## 1. Install

1. Download **AutoEdit-0.1.0-arm64.dmg** from the [latest release](https://github.com/ywzscb24rw-art/autoedit/releases/latest) and open it.
2. Drag **AutoEdit** into the **Applications** folder.
3. Open AutoEdit from Applications.

## 2. Let macOS open it (one time only)

AutoEdit is an early beta and isn't registered with Apple yet, so the first time you open it, macOS will say it *"cannot verify that AutoEdit is free of malware"*. That's expected. To open it anyway:

1. Click **Done** (not "Move to Trash").
2. Open **System Settings → Privacy & Security**.
3. Scroll down. Next to *"AutoEdit was blocked to protect your Mac"*, click **Open Anyway**.
4. Enter your Mac password, then click **Open Anyway** again.

After this, AutoEdit opens normally.

## 3. Add your Claude API key

AutoEdit uses Claude to decide what to keep and what to cut. You use your own Anthropic account, so you only pay for what you use. A typical video costs a few cents.

1. Go to **platform.claude.com** and sign up or log in.
2. Under **Billing**, add credit. **$5 is plenty for the beta.**
3. Under **API Keys**, click **Create Key**, name it "AutoEdit", and copy it. It starts with `sk-ant-`.
4. In AutoEdit, paste the key and click **Save key**.

Your key is stored only on your Mac.

## 4. Download the speech model

Click **Download speech model** in AutoEdit. It's about 1.6 GB and takes a few minutes, one time only. Transcription runs entirely on your Mac.

## 5. Record or upload

- **Record:** choose a video type and mode, click **Start recording**, and pick what to share. The first time, macOS asks for permission to record your screen and use your microphone. Allow both. If you clicked "Don't Allow", turn AutoEdit on in **System Settings → Privacy & Security → Screen & System Audio Recording** (and **Microphone**), then reopen AutoEdit.
- **Upload:** click **Upload a video** to edit something you've already filmed.

**Video types:**
- **Screen recording:** tutorials and walkthroughs.
- **Talking head:** you on camera. Cut tight, with punch-in zooms and captions.
- **Vlog:** keeps the footage between lines (reactions, scenery).

**Modes:**
- **Clean edit:** one tight video.
- **Find clips:** 3–5 short vertical clips with strong openings.

When it's done, click any word in the transcript to cut it or bring it back, then click **Render**.

---

## What we'd love feedback on

- Did any cut feel wrong, such as a word chopped off, a good line removed, or a retake kept?
- Were the clips worth posting? Was the opening of each clip strong?
- Anything confusing, slow, or broken?

Use **Help → Send feedback** in the menu bar. It opens a pre-filled GitHub issue with your app version and recent log (you'll need a free GitHub account). Issues are public, so leave out anything private. Screen recordings of problems are gold: drag them into the issue.

## Known limits in this beta

- Apple-chip Macs only (no Intel).
- No automatic updates. **Help → Check for updates** opens the downloads page.
- Long 4K videos take a few minutes to prepare the first time.
- Your projects live in *~/Library/Application Support/AutoEdit*. Use **Help → Open projects folder** to find them.
