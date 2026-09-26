"""Builtin style-dictionary content seeded by data migration.

Each entry is (field, name, definition, example). The definitions and
examples are injected into generation prompts when a project's style values
resolve to the term, so the model shares the author's vocabulary.
"""

BUILTIN_TERMS = [
    # --- Genre ---------------------------------------------------------------
    ("genre", "Fantasy", "Magic, myth, or the impossible treated as real inside the story world; the worldbuilding itself carries the reader's sense of wonder.", "A blacksmith discovers the forge answers him back, and every blade he shapes remembers its victim."),
    ("genre", "Xianxia", "Chinese cultivation fantasy: protagonists train through mortal limits toward immortality, with martial sects, qi refinement, and clearly tiered power.", "A crippled disciple refines qi from a stolen spirit herb and enters the sect tournament under a false name."),
    ("genre", "LitRPG", "Explicit game-like mechanics — levels, stats, skills, quests — drive character growth and plot stakes on the page.", "Each dungeon clear grants one skill point, and spending it wrong last time is why her sister is captured."),
    ("genre", "Science Fiction", "Speculation grounded in science or technology that examines consequences as much as inventions.", "A relay-station crew receives a distress signal from a ship that has not launched yet."),
    ("genre", "Cyberpunk", "High tech, low life: AI, megacorporations, and body modification against social decay and inequality.", "A courier with a cracked ocular implant smuggles stolen memories through the neon sprawl."),
    ("genre", "Mystery", "A central question — usually a crime — drives the plot; clues are planted fairly and revealed at the climax.", "The study was locked from inside, the ledger is missing, and one guest arrived at the manor twice."),
    ("genre", "Thriller", "Sustained suspense with ticking-clock stakes; the protagonist is hunting or hunted.", "She has twelve hours to prove the forgery before the warrant makes her the prime suspect."),
    ("genre", "Romance", "A central love story carried by emotional beats toward a satisfying resolution.", "Rival chefs share one kitchen, one supplier war, and a betrayal neither can forgive."),
    ("genre", "Horror", "Fear engineered through dread, atmosphere, and loss of control rather than gore alone.", "Every mirror in the house runs two seconds slow, and today the reflection blinked first."),
    ("genre", "Historical", "A real or meticulously reconstructed past in which period-accurate detail does narrative work.", "1920s Shanghai: telegraph codes, treaty-port politics, and a jazz club where the unions meet."),
    ("genre", "Adventure", "Journey-driven spectacle where each new setting raises the physical stakes.", "The map burns from the edges as the airship loses altitude over the salt wastes."),
    ("genre", "Slice of Life", "Small-scale, character-first storytelling where texture and routine replace escalation.", "A tea shop, a rainy Tuesday, and the regular who has never once ordered from the menu."),
    # --- Subgenre --------------------------------------------------------------
    ("subgenre", "Court Intrigue", "Power brokered in drawing rooms and shadow deals; information and patronage are the sharpest weapons.", "The duke raises a toast to loyalty and names no name; every guest hears their own secret."),
    ("subgenre", "Progression Fantasy", "Measured, earned power growth with clear training arcs, costs, and payoffs.", "Two hundred chapters from lantern-boy to blade-saint, and every technique is paid for in scars."),
    ("subgenre", "Portal Fantasy", "A protagonist crosses into another world; the rules of the crossing and the way home shape the plot.", "She dies on the midnight subway and wakes as the villainess of the novel she abandoned."),
    ("subgenre", "Cozy Mystery", "Low-violence mystery in an intimate community, solved by an observant amateur.", "The village baker deduces that the poison was in the sourdough starter, not the jam."),
    ("subgenre", "Post-apocalyptic", "After collapse: survival logistics, improvised social orders, and what people become inside them.", "Water is currency, and the convoy elects its new leader by ration share."),
    ("subgenre", "Space Opera", "Large-scale interstellar drama of empires, fleets, and families across light-years.", "The empress's bastard takes command of the last dreadnought at the rim."),
    ("subgenre", "Urban Fantasy", "Magic hidden inside, or bleeding into, a recognizably modern city.", "The pawnshop buys memories by the hour; the detective's badge is a ward."),
    # --- Tone --------------------------------------------------------------
    ("tone", "Grim", "Bleak stakes and real costs; victories leave scars and nobody walks away whole.", "They win the battle; the casualty roll names half the company and both of the brothers."),
    ("tone", "Hopeful", "Darkness is real, but kindness, courage, and repair win in the end.", "The city burns, yet the bucket lines are singing."),
    ("tone", "Epic", "Mythic scale and elevated language; the stakes rise with the prose.", "The sky itself takes sides as the old kings wake beneath the mountain."),
    ("tone", "Mysterious", "Information is withheld, misdirected, and revealed with deliberate intent.", "The letter is signed in her own handwriting — and dated tomorrow."),
    ("tone", "Witty", "Verbal play, irony, and comic timing layered over real stakes.", "'You're poisoning the tea.' 'I'm sweetening it. Aggressively.'"),
    ("tone", "Cozy", "Low threat and high comfort; warmth and small pleasures live on the page.", "The gravest crime this week is the theft of the scone recipe."),
    ("tone", "Melancholy", "Beauty sharpened by loss; an elegiac, reflective register.", "A year on, she still sets out two cups, and pours both."),
    # --- Point of view --------------------------------------------------------------
    ("pov", "Third Person Limited", "Narration rides one character's perceptions per scene; the reader knows only what they know.", "We hear Marek's dread through the whole dinner, and never the assassin's, until the knife is bare."),
    ("pov", "Third Person Omniscient", "The narrator sees all minds and can range across the world at will.", "Neither of them knew the treaty had been burned an hour before they signed it."),
    ("pov", "First Person", "I-narration where voice, bias, and unreliability are the engine.", "I want to tell you I hesitated. I didn't."),
    ("pov", "Second Person", "You-narration; rare and immersive, used for disorientation or complicity.", "You tell yourself the package is already paid for as you nail it shut."),
    # --- Narrative tense --------------------------------------------------------------
    ("tense", "Past Tense", "Events narrated as completed; the default register for most serialized fiction.", "She opened the door, and the room remembered her."),
    ("tense", "Present Tense", "Events unfold as they are read; immediacy at the cost of hindsight.", "She opens the door, and the room remembers her."),
    # --- Pacing --------------------------------------------------------------
    ("pacing", "Slow Burn", "Long arcs and deep interiority where payoff is earned across many chapters.", "Three chapters of courtly dance pass before the first blade is drawn."),
    ("pacing", "Balanced", "Steady alternation of pressure and release; scene and sequel in rhythm.", "Every raid is followed by a reckoning at the campfire."),
    ("pacing", "Breakneck", "Short scenes, hard cuts, and chapter-end hooks; momentum outranks texture.", "End every chapter on the door breaking."),
    ("pacing", "Episodic", "A self-contained case or quest per arc with a loose serial spine underneath.", "A new client each week, while the debt ledger grows quietly in the background."),
    # --- Protagonist type (archetypes) --------------------------------------------------------------
    ("protagonist", "Underdog", "Starts weakest in the room; growth is the promise, and every gain is paid for.", "The lantern-boy sweeps the sect's floors and drills every footwork form in secret."),
    ("protagonist", "Reluctant Hero", "Thrust into stakes they keep trying to refuse, and refusal keeps getting costlier.", "She burns the summons; by morning it has rewritten itself on her door."),
    ("protagonist", "Chosen One", "Marked by prophecy or fate; the arc is living up to — or rejecting — the label.", "The sword chose her, and everyone but her treats that as settled law."),
    ("protagonist", "Anti-hero", "Wins by questionable means; the reader roots for and against them at once.", "He poisons the general, frames the alibi, and sleeps fine — mostly."),
    ("protagonist", "Villain Protagonist", "The story rides the antagonist's mind; sympathy without excuse.", "She bills the city for the fire she set, and the council pays."),
    ("protagonist", "Everyman", "Ordinary competence against extraordinary circumstances; relatability is the anchor.", "A bus driver, a shotgun, and the end of the world."),
    ("protagonist", "Trickster", "Wins by wit, cons, and rule-bending; plans nested inside plans.", "He sells the same bridge to three guilds — and needs all three to show up."),
    ("protagonist", "Mentor", "The teacher's journey; stakes are measured through students and legacy.", "His last student will face the thing that broke him."),
    # --- Protagonist trait (gender, status — mixes with any archetype) --------------------------------
    ("protagonist_trait", "Female Protagonist", "The lead is a woman; her womanhood shapes how the world treats her without defining her whole arc.", "The court underestimates the new spymaster — she counts on it."),
    ("protagonist_trait", "Male Protagonist", "The lead is a man.", "He inherits the debt, the blade, and the feud in the same week."),
    ("protagonist_trait", "Non-binary Protagonist", "The lead lives outside the binary; the world's categories misfit them and the prose respects them.", "Every form in the empire has two boxes; they check neither and pay the clerk extra."),
    ("protagonist_trait", "Wealthy Protagonist", "Rich from page one; money solves problems loudly and creates worse ones quietly.", "She buys the loan her rival holds — then keeps the payments coming, why ruin a good leash?"),
    ("protagonist_trait", "Poor Protagonist", "Every expense is a decision and every windfall is a plot event.", "The reward for the quest is a year of rent; the danger is the rent collector who came along."),
    # --- Novel tags (NovelUpdates-style trope flags; mix freely) --------------------------------------
    ("tags", "Gender Bender", "A lead lives as, becomes, or is transformed into another gender — body swap, transmigration, disguise, or transition; identity friction drives the story.", "He wakes in the duke's daughter's body, and the signet ring still answers to his blood."),
    ("tags", "Reincarnation", "Death begins a new life with memories intact; the past life is knowledge to spend.", "The hero dies on page one and is reborn as the villain's overlooked third son."),
    ("tags", "Transmigration", "A soul crosses into another body or world, often a story the soul already knows.", "She wakes inside the novel she abandoned at chapter twelve — as cannon fodder on page three."),
    ("tags", "System", "A game-like interface grants quests, levels, and penalties; the System has opinions.", "DING. New quest: survive until dawn. Reward: one skill point. Penalty: everything."),
    ("tags", "Villainess", "The lead inhabits the villainess's doomed role and rewrites her script before the execution flag falls.", "The doom flag is three chapters away; the tea party is her first move."),
    ("tags", "Weak to Strong", "The lead starts at the bottom of the power curve and earns every tier on screen.", "From last-place disciple to tournament dark horse, one bruise at a time."),
    ("tags", "Overpowered Protagonist", "The lead is already the strongest in the room; tension comes from restraint, boredom, and what power cannot fix.", "He seals his cultivation to taste the fight — the sealing is the story."),
    ("tags", "Multiple POV", "Chapters alternate between several viewpoint characters; dramatic irony compounds.", "The thief's chapter ends where the inquisitor's begins — same alley, opposite purposes."),
    ("tags", "Harem", "Multiple love interests orbit the lead, and the orbit itself has politics.", "Three letters, three seals, one answer he cannot afford to send."),
    ("tags", "Slow Romance", "Romance builds across arcs; the wait is the point.", "Two hundred chapters of shared umbrellas before anyone says the word."),
    ("tags", "Yandere", "Devotion that tips into obsession; love as a threat vector.", "He keeps a ledger of everyone who spoke to her, and last week it gained columns."),
    ("tags", "Survival", "Scarcity and elimination pressure; the environment is the antagonist.", "Water is the scoreboard, and the sun is winning."),
    ("tags", "Slow Life", "Low-stakes comfort and competence; the plot is a garden that grows.", "Today's crisis is a sourdough starter, and there will be tea about it."),
]
