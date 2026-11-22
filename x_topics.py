"""Conservative, local-only topic hints from imported text; no URL or media retrieval."""
import html
import re


UNKNOWN = 'Inhalt nicht ausreichend erkennbar'
RULES_VERSION = 'local-rules-v1'

RULES = {
    'KI & Agenten': (
        ('ai agent', 'ai agents', 'artificial intelligence', 'machine learning',
         'llm', 'anthropic', 'claude', 'chatgpt', 'neural network', 'deep learning',
         'ki-agenten', 'künstliche intelligenz', 'ai engineer'),
        ('ai', 'agents', 'prompt', 'model', 'python')),
    'Coding & Tools': (
        ('obsidian', 'javascript', 'typescript', 'software development', 'open source',
         'programming', 'git repository', 'developer tools', 'coding tools'),
        ('code', 'coding', 'github', 'python', 'repo', 'software', 'plugin')),
    'Gesundheit & Training': (
        ('neck', 'back pain', 'strength training', 'mobility', 'workout', 'fitness',
         'nutrition', 'muscle', 'rückenschmerzen', 'nacken', 'krafttraining',
         'gesundheit', '低冲击训练', '训练', '体格', 'ظهرك', 'رقبتك'),
        ('training', 'sleep', 'exercise', 'protein')),
    'Finanzen & Wirtschaft': (
        ('stock market', 'stocks', 'trading', 'candlestick', 'portfolio', 'investing',
         'investment', 'aktien', 'finanzen', 'backtest', 'quant', 'inflation',
         'wirtschaft', 'dividend'),
        ('finance', 'financial', 'market', 'money', 'investor')),
    'Politik & Gesellschaft': (
        ('politics', 'politique', 'government', 'parliament', 'election', 'propaganda',
         'gesellschaft', 'politische', 'bundestag', 'migration', 'lfi', 'macron'),
        ('state', 'france', 'population', 'society', 'democracy')),
    'Produktivität & Lernen': (
        ('productivity', 'time management', 'game theory', 'daily routine',
         'study habits', 'learning strategy', 'tagesplan', 'produktivität',
         'zeitmanagement', 'lernstrategie'),
        ('goals', 'habits', 'focus', 'lernen', 'routine')),
    'Familie & Alltag': (
        ('emergency prep', 'basic prep', 'shelf-stable food', 'water per person',
         'parenting', 'childcare', 'family routine', 'erziehung', 'familienalltag'),
        ('family', 'children', 'household', 'notfallvorsorge')),
    'Musik, DJ und Audio': (
        ('piano', 'music', 'guitar', 'dj mix', 'synthesizer', 'musik', 'klavier',
         'keystutorials', 'sound design', 'ableton'),
        ('audio', 'melody', 'chord', 'song')),
    'Wohnen & Gestaltung': (
        ('interior design', 'home decor', 'furniture', 'architecture', 'inneneinrichtung',
         'wohnzimmer', 'möbel', 'raumgestaltung'),
        ('living room', 'design', 'wohnung')),
}


def matches_topic_phrase(text, phrase):
    if all(ord(c) < 0x250 for c in phrase):
        return re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text) is not None
    return phrase in text


def classify_topic(text):
    if not isinstance(text, str):
        return UNKNOWN
    text = re.sub(r'https?://\S+', ' ', html.unescape(text)).casefold()
    text = re.sub(r'\s+', ' ', text).strip()
    scores = []
    for topic, (strong, supporting) in RULES.items():
        score = sum(3 for phrase in strong if matches_topic_phrase(text, phrase))
        score += sum(1 for phrase in supporting if matches_topic_phrase(text, phrase))
        scores.append((score, topic))
    scores.sort(reverse=True)
    best, runner_up = scores[:2]
    return best[1] if best[0] >= 3 and best[0] - runner_up[0] >= 2 else UNKNOWN
