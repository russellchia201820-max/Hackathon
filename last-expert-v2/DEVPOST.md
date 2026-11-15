# Last Expert: an AI that interviews experts and captures what they don't know they know

## The problem
Experts can't easily explain what they do. Their skill is *tacit*: "you just get a feel for it". When they retire or move on, that knowledge walks out the door, and their handover documents skip exactly the steps a beginner needs.

## What we built
Last Expert is a live video call with an AI apprentice. The expert says "I want to teach you how to do X", and the apprentice interviews them like a maximally curious trainee who lets **no assumption pass**. Beside the call, a live panel shows:
- **Captured knowledge** building up as structured cards: steps, decision rules, cues, mistakes, edge cases, tools and terms, and quality checks.
- **Assumptions spotted:** every vague phrase ("usually", "you just know"), undefined term, skipped step or missing *why*, quoted in the expert's own words. Each one goes open → asked → resolved ✓ as the apprentice follows up.
- **A coverage bar** for the task checklist.

At the end, **Generate playbook** produces a beginner-ready training document with an honest "Not yet covered" section. **Ask the expert** lets a beginner question an AI copy of the expert. It answers *only* from what the expert said, cites its sources, and flags anything the expert never covered.

## How it works
- **Two loops.** A fast interviewer (Claude Haiku 5.5) speaks one short question per turn in about a second. A slow analyst (Claude Sonnet 5.5) runs in the background after every answer. It extracts atomic knowledge items, spots assumptions, and writes follow-up questions using Cognitive Task Analysis and Critical Decision Method techniques ("Tell me about a time this went wrong", "What's the first thing you notice when…").
- **A deterministic selector** ranks the open gaps and feeds the best three, with anchor quotes, back to the interviewer before each reply. That lets it bridge naturally: "Earlier you said 'you just know'. What are you actually seeing?"
- **Tool calling:** the agent pins key insights, asks to see things on camera (Claude vision then describes the frame), and ends the interview when coverage is complete. Every tool call appears on the panel.
- **Voice and face:** ElevenLabs Scribe realtime STT, ElevenLabs TTS, LiveKit Agents and a Synthesia interactive avatar.

## Evidence
Against a simulated expert holding 42 hidden facts about identifying minerals, Last Expert captured **78.6%** of them in 15 questions. On an everyday task (a student's commute home, 20 hidden facts) it captured **92.5% in 12 questions**, asking for exactly the specifics a newcomer needs: where home is, which stop, which route, where to park. The expert played its part like a real one: jargon, skipped steps, "you just know".

## Built with
Anthropic Claude (Haiku 5.5, Sonnet 5.5) · ElevenLabs Scribe v2 Realtime STT · ElevenLabs TTS · LiveKit Cloud and LiveKit Agents · Synthesia interactive avatar (Anam as fallback) · FastAPI · vanilla JS
