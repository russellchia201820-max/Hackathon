# Last Expert
 
**AI that watches an expert work, asks why, and turns their unwritten know-how into an illustrated manual and an avatar mentor apprentices can question.**
 
Built in 24 hours at **Imperial AI Hack 2026** · Track 03: Voice, Vision & Generative Media
 
---
 
## The problem
 
Every industry runs on knowledge that was never written down: the field engineer who knows a pump is failing by its sound, the lab technician who knows the old instrument reads high on cold mornings, the operator who knows which step in the official procedure everyone actually skips.
 
When those people retire or leave, that knowledge goes with them.
 
A standard AI can't help, because this knowledge isn't in any training data. Ask a chatbot how to do a task and you get the textbook version. What you need is **how this person does it, at this site, and why they do it differently from the book.**
 
## What Last Expert does
 
1. **Film** — the expert performs a task on camera for 1–3 minutes, talking as they go.
2. **Interview** — the AI watches the footage, spots the steps the expert *did* but didn't *explain*, and asks about them by voice: *"You tapped the valve before opening it. Why?"*
3. **Manual** — it generates a step-by-step guide with an illustration and narration for each step, with the expert's reasons attached and the site-specific quirks highlighted.
4. **Mentor** — an avatar grounded in the manual and interview answers an apprentice's questions by voice.
Step 2 is the core idea: **the AI uses what it saw to decide what to ask.** Video captures what experts do; the interview captures why. Each fills the other's gap.
 
## Why it's different from asking a chatbot
 
| Knowledge layer | Example | Does a standard AI know it? |
|---|---|---|
| General principles | How a mud balance measures density | Yes |
| Company procedures | This firm's written drilling standard | No, but it's documented internally |
| **Personal and site know-how** | **"The gauge on pump 2 reads high, so knock 50 psi off"** | **No, and it's written down nowhere** |
 
Last Expert targets the third layer. The output highlights every line that differs from the generic procedure, because those lines are the product.
 
## Example: drilling-mud density check
 
A mud engineer weighs drilling fluid with a mud balance many times a shift. The written procedure is five lines; the skill is in the details:
 
- Tapping the cup to knock out trapped air (air makes mud read light)
- Waiting for mud to squeeze from the lid hole (proves the cup is full)
- Wiping the outside clean (residue makes it read heavy)
- Checking calibration against fresh water at 8.33 lb/gal
Last Expert catches each of these on video, asks about them, and builds them into the manual and the mentor.
 
## Architecture
 
```
 Video + audio ──► Transcription ──► Step segmentation ──► Gap detection
                    (ElevenLabs)      (Claude vision)     "what did they do but not explain?"
                                                                   │
                                                                   ▼
                                                    Voice follow-up interview
                                                         (ElevenLabs)
                                                                   │
                                                                   ▼
                                Manual builder ◄──────── Knowledge record
                     (step text + fal illustrations       (steps, reasons,
                      + ElevenLabs narration)              quirks, warnings)
                                                                   │
                                                                   ▼
                                                     Avatar mentor (Anam)
                                                  grounded Q&A for apprentices
```
 
## Tech stack
 
| Layer | Tool |
|---|---|
| Video understanding & reasoning | Claude (Anthropic API) |
| Transcription, interview voice, narration | ElevenLabs |
| Step illustrations | fal |
| Avatar mentor | Anam |
| Backend | Python |
| Frontend | Web app |
 
## APIs, models and datasets used
 
- **Anthropic Claude API** — frame analysis, step segmentation, gap detection, question generation, manual writing, mentor grounding
- **ElevenLabs API** — speech-to-text, text-to-speech for interview and narration
- **fal API** — image generation for step illustrations
- **Anam API** — real-time interactive avatar
- **Datasets** — none; all demo content was recorded by the team during the hackathon
## Getting started
 
```bash
git clone https://github.com/<your-org>/last-expert.git
cd last-expert
cp .env.example .env   # add your API keys
pip install -r requirements.txt
python app.py
```
 
Required environment variables:
 
```
ANTHROPIC_API_KEY=
ELEVENLABS_API_KEY=
FAL_KEY=
ANAM_API_KEY=
```
 
## Demo
 
1. A volunteer performs a short task on camera with one deliberate personal quirk.
2. Last Expert asks them about the quirk.
3. Their manual appears, with the quirk and its reason highlighted against the generic procedure.
4. A second person questions the avatar mentor live.
## Roadmap
 
- Multi-session capture to build a full expert profile over weeks
- Disagreement detection when two experts do the same task differently
- Export to existing training and maintenance systems
- Pilots with retiring field engineers in energy and utilities
