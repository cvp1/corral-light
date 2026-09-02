# Passing work between assistants

Corral Light puts several assistants side by side. On their own they are
separate: their own process, their own login, their own memory of the
conversation. This page is about the moments you want them to *share*
something, and which of the five ways to do that fits the moment.

The short version:

| You want… | Do this | What you get |
|---|---|---|
| One assistant to read a note or file you have | ⌘K, pick the note, ↵ | Grounding. A coding assistant gets the path and opens it through its own approval; a chat-only one gets a quoted excerpt. |
| A second opinion on one answer | ⌘K, pick the *other* pane, ⇧↵ | That pane's last answer lands in your composer, quoted and named. You add the question. Nothing is sent until you press send. |
| Independent answers from everyone | Type once, press ⌘↵ | The same prompt goes to every live pane. No pane sees another's answer. |
| The answers to fight it out | Press **⇄ Cross-feed** | Every pane gets every other pane's answer under a preamble you can edit. Round two. Press again for round three. |
| An assistant to consult another one *itself* | Ask it to ("run this by Grok") | It shells out to the other tool, through the same approval you give any command, and reads the answer back. |

Everything below is the long version: when each one earns its place, what it
costs, and the rules that hold in all of them.

---

## Attach a note — give one assistant something to read

**When.** You have a file or a note that the answer depends on: a design doc,
yesterday's log, a checklist. The assistant should work from it, not from
memory of it.

**How.** ⌘K, type a few letters of the note, press ↵. With no pane focused,
⇧↵ opens a new pane in the note's folder and attaches it there.

**What you gain.** Grounding without pasting. A coding assistant receives the
*path* and opens the file through its own permission gate, so the read is
visible in the transcript and refusable in the rail. A chat-only assistant
(Ollama) receives a bounded quoted excerpt, because it has no file access and
a path would be a dead end.

**What it costs.** Nothing until you send. The attachment is text in your
composer; read it, add your question, then send.

## Quote — hand one answer to one other assistant

**When.** One assistant has answered and you want a specific other one to
check it, extend it, or take it further. You know who should look at it and
what you want asked.

**How.** Focus the pane that should *receive* the answer. ⌘K, pick the pane
that *gave* it, press ⇧↵. The row's hint tells you where it will land before
you press. Plain ↵ on that row just focuses it.

**What you gain.** Control of the framing. The answer arrives quoted,
attributed by lane ("From Grok — …"), with your cursor after it. You write the
ask: "Is this right?", "Do the opposite", "Turn this into a test." One
assistant's work becomes another's input on your terms.

**What it costs.** One send. A long answer is cut at a fixed length and marked
truncated, so you never paste a novel by accident. Quote refuses a pane that
has not answered yet, and it will never quote into or out of an SSH terminal
pane, where pasted text is a command.

## Fan-out — ask everyone, blind

**When.** You do not know which assistant is strongest for this question, or
you want independent takes before anyone is influenced. Design questions,
"what am I missing", anything with more than one defensible answer.

**How.** Type the question in any pane. Press ⌘↵ instead of ↵. The toast says
how many panes took it and names any that refused.

**What you gain.** N answers for one typing, and *independence*: no pane sees
another's answer, so agreement means something and disagreement is real. This
is the blind half of a panel.

**What it costs.** N turns of whatever each lane costs you. Plain ↵ is still
one pane; the chord is deliberate so you cannot broadcast by accident. Only
live, on-screen panes with a chat composer take part; minimized, paused and
terminal panes do not.

## Cross-feed — make them argue

**When.** After a fan-out, when the answers disagree or you want them stress
tested. Also when they *agree* and you want to know whether that is
convergence or three models sharing a blind spot.

**How.** Press **⇄ Cross-feed** in the rail. A dialog shows the preamble each
pane will receive above the others' answers; edit it or accept the default,
which asks each arm to attack the others, say what it now rejects, and end
with a revised answer. Press again later for round three.

**What you gain.** A review you would otherwise pay for. Retractions, sharper
claims, and a visible record of who moved: each composed prompt lands in the
pane as an ordinary user turn, so you can read exactly what every arm was
given and how it responded.

**What it costs.** N more turns, each carrying the other answers as context.
Cross-feed refuses to run at all while any pane is still writing, rather than
run a round with a missing arm nobody was told about. Fix the slow pane, or
close it, then press again.

## Ask an assistant to consult another — the hand-off it does itself

**When.** A coding assistant is mid-task and *it* is the one that should
decide a second opinion is worth having. You say "run this by Grok" and let it
go.

**How.** Just ask. The assistant shells out to the other vendor's command-line
tool, the same way it runs any command, and the rail stops it for your
approval first. Allow once, and it reads the answer back and continues.

**What you gain.** No relaying. The assistant carries its own context into the
question and folds the answer into its own work. It inherits the harness it
was started with, so the consulted model answers inside the same guardrails.

**What it costs.** One approval, and a subscription you already have. The
other tool must be installed and signed in on the box. This is the pattern the
three verbs above grew out of; use it when the assistant, not you, is the one
who should be deciding to ask.

---

## The panel recipe

The verbs compose into one routine that replaces "open three tabs and paste":

1. Open the lanes you trust for the question. Three is the sweet spot.
2. Type the question once, press ⌘↵. Wait for every pane to finish.
3. Read the answers. Note where they disagree.
4. Press ⇄. Read who retracted, who dug in, and what got sharper.
5. Pick a judge. Focus one pane, quote the others into it with ⇧↵, and ask for
   a verdict. Or open a fresh pane so the judge has no stake.

A round of three arms takes about a minute and a half on ordinary questions.
Everything stays in the panes, so you can scroll back through the argument
later.

## The rules that hold in every case

- **Nothing leaves without send.** Attach and quote only put text in your
  composer. Fan-out and cross-feed send, but only what you typed or approved
  in the dialog.
- **The rail is unchanged.** A fanned-out prompt stops at the same approval a
  typed one would. No verb grants a pane anything it did not already have.
- **You can always see what was sent.** Composed prompts are ordinary user
  turns in the transcript, attributed by lane.
- **Terminal panes stay out.** An SSH pane is never a source or a target; text
  on a command line is a command.
- **Bounded.** Quotes are cut at a fixed length and say so. Fan-out and
  cross-feed respect each pane's queue and refuse past it.
