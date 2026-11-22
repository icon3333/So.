import unittest
from x_topics import classify_topic, UNKNOWN


class TopicTests(unittest.TestCase):
    def test_clear_texts_in_existing_topics(self):
        examples = {
            'Anthropic Claude AI agents roadmap': 'KI & Agenten',
            'Obsidian plugins and Python coding tools': 'Coding & Tools',
            'Give your neck a break': 'Gesundheit & Training',
            '低冲击训练 帮助保持体格': 'Gesundheit & Training',
            'خفف الآم ظهرك و إنحناء رقبتك': 'Gesundheit & Training',
            'Stock market trading and backtest': 'Finanzen & Wirtschaft',
            'French politique and parliament election': 'Politik & Gesellschaft',
            'Game theory and habits for your goals': 'Produktivität & Lernen',
            'Basic prep: shelf-stable food and water per person': 'Familie & Alltag',
            'A piano tutorial with awesome music': 'Musik, DJ und Audio',
            'Interior design with furniture': 'Wohnen & Gestaltung',
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(classify_topic(text), expected)

    def test_no_guesses_from_urls_emojis_authors_or_ambiguous_signals(self):
        for text in [None, '', '😂😂😂 https://t.co/xyz', 'Russians 🌚',
                     'https://example.org/stock-market-trading', 'AI agents and piano music']:
            with self.subTest(text=text):
                self.assertEqual(classify_topic(text), UNKNOWN)

    def test_repeated_words_do_not_outvote_distinct_topic_signals(self):
        self.assertEqual(classify_topic(('AI ' * 100) + 'stock market trading backtest'),
                         'Finanzen & Wirtschaft')
