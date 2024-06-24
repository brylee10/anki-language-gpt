import logging
import os
from datetime import datetime

import aiohttp
import openai
from translation_utils import ExampleSentence, OutputFormat, get_all_languages_lower

# Remove ancillary characters from select GPT responses
STRIP_CHARACTERS = ' \n\t\'".;:!?'

openai.api_key = os.getenv('OPENAI_API_KEY')
client = openai.OpenAI()


class OpenAIGenerator:
    """Generate sample sentences with OpenAI GPT-4o"""

    def __init__(self, language: str):
        self.tokens = []
        self.language = language

    async def query_romanization(self, session: aiohttp.ClientSession, word: str) -> str:
        """Queries GPT-4o for romanization of a word"""
        # Timing
        start = datetime.now()
        response = await session.post(
            'https://api.openai.com/v1/chat/completions',
            # headers={'Authorization': f'Bearer {os.getenv('OPENAI_API_KEY')}'},
            json={
                'model': 'gpt-4o',
                'messages': [
                    {
                        'role': 'system',
                        'content': (
                            f'You are a helpful {self.language} instructor. Be very concise. '
                            'You can use incomplete sentences.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': (
                            'Give the romanization (i.e. pinyin, romanji equivalent for '
                            f'language {self.language}) for this word: {word}. Put appropriate '
                            'spaces, accents, and diacritics. Do not capitalize romanizations.'
                        ),
                    },
                ],
                'temperature': 1.0,
                'max_tokens': 50,
                'top_p': 1,
                'n': 1,
                'frequency_penalty': 0.0,
                'presence_penalty': 0.0,
            },
        )

        # Track usage
        data = await response.json()
        # Catch errors from response
        if response.status != 200:
            raise RuntimeError(f'Error in query_romanization: {response}')

        tokens = data['usage']['total_tokens']
        self.tokens.append(tokens)

        # GPT-4o
        pinyin = data['choices'][0]['message']['content'].strip(' \n')

        end = datetime.now()
        logging.debug(
            f'GPT-4o gave pinyin for {word} as {pinyin} in {(end-start).total_seconds()} '
            f'seconds with {tokens} tokens used'
        )

        return pinyin

    async def query_translation(self, session: aiohttp.ClientSession, word: str) -> str:
        """Queries GPT-4o for a translation"""
        # Timing
        start = datetime.now()

        response = await session.post(
            'https://api.openai.com/v1/chat/completions',
            # headers={'Authorization': f'Bearer {os.getenv('OPENAI_API_KEY')}'},
            json={
                'model': 'gpt-4o',
                'messages': [
                    {
                        'role': 'system',
                        'content': (
                            f'You are a helpful {self.language} instructor. '
                            'Be very concise. you can use incomplete sentences.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': (
                            f'Translate this {self.language} word or phrase into English in an '
                            f'idiomatic, not just literal, way: {word}.'
                        ),
                    },
                ],
                'max_tokens': 50,
            },
        )

        # Track usage
        data = await response.json()
        if response.status != 200:
            raise RuntimeError(f'Error in querying translation for {word}: {data}')
        tokens = data['usage']['total_tokens']
        self.tokens.append(tokens)

        # GPT-4
        translation = data['choices'][0]['message']['content'].strip(' \n')

        end = datetime.now()
        logging.debug(
            f'GPT-4o translated {word} as {translation} in {(end-start).total_seconds()} '
            f'seconds with {tokens} tokens used'
        )

        return translation

    async def generate_sample_sentences(
        self,
        session: aiohttp.ClientSession,
        word: str,
        nsentences: int,
    ) -> list[ExampleSentence]:
        """Generates `nsentences` sample sentences from `word` using GPT."""
        # Timing
        start = datetime.now()
        prompt = (
            f'Write a short, illustrative phrase in {self.language} using the word {word} '
            f'followed by its English translation. Formatted as {self.language} first, '
            'then english on different lines'
            f"i.e.: '${self.language} \\n English'. Do not forget either the {self.language} "
            'or the english translation! '
        )

        # Generates n = nsentences * 2 completions to the prompt with high randomness
        # (temperature = 1.0) to reduce duplicates adn select for the "best" sentences
        # among these.
        # Generates extra sentences to account for duplicates and sentences that do not
        # match the format (foreign language then English)
        # Max tokens across 3 completions ~200 tokens.
        response = await session.post(
            'https://api.openai.com/v1/chat/completions',
            # headers={'Authorization': f'Bearer {os.getenv('OPENAI_API_KEY')}'},
            json={
                'model': 'gpt-4o',
                'messages': [
                    {
                        'role': 'system',
                        'content': f'You are a {self.language} language translator.',
                    },
                    {'role': 'user', 'content': f'{prompt}'},
                ],
                'temperature': 1.0,
                'max_tokens': 65,
                'top_p': 1,
                'n': nsentences * 2,
                'frequency_penalty': 0.0,
                'presence_penalty': 0.0,
            },
        )

        # Track usage
        data = await response.json()
        if response.status != 200:
            raise RuntimeError(f'Error in generating sample sentences for {word}: {data}')
        tokens = data['usage']['total_tokens']
        self.tokens.append(tokens)

        # GPT-4
        sentences = [c['message']['content'].strip(' \n') for c in data['choices']]
        logging.debug(f'GPT-4o generated sentences (for word {word}): {sentences}')

        example_sentences: list[ExampleSentence] = []
        prev_sentences = set()
        for sentence in sentences:
            # Each sentence should be `\n` separated with foreign language then english
            # GPT does not always follow this format, so we skip those that do not match
            foreign_english = sentence.split('\n')
            # Remove any splits which are only whitespace, `\n`, or empty strings
            foreign_english = [s.strip() for s in foreign_english if s.strip()]
            if len(foreign_english) != 2:
                continue
            foreign_lang_example, english = foreign_english
            # Ignore duplicates
            if foreign_lang_example in prev_sentences:
                continue
            prev_sentences.add(foreign_lang_example)

            example_sentences.append(
                ExampleSentence(foreign_lang=foreign_lang_example, english=english)
            )

        example_sentences = example_sentences[:nsentences]
        end = datetime.now()
        logging.debug(
            f'GPT-4o generated {len(example_sentences)} sentences for {word} in '
            f'{(end-start).total_seconds()} seconds with {tokens} tokens used'
        )

        return example_sentences

    async def generate_intuitive_explanation(
        self, session: aiohttp.ClientSession, word: str
    ) -> str:
        """Generates an intuitive explanation/breakdown of the word/phrase"""
        # Timing
        start = datetime.now()
        max_tokens = 50

        response = await session.post(
            'https://api.openai.com/v1/chat/completions',
            # headers={'Authorization': f'Bearer {os.getenv('OPENAI_API_KEY')}'},
            json={
                'model': 'gpt-4o',
                'messages': [
                    {
                        'role': 'system',
                        'content': (
                            f"You're a helpful {self.language} instructor. "
                            'Be very concise. You can use incomplete sentences.'
                        ),
                    },
                    {
                        'role': 'user',
                        'content': (
                            f'In {max_tokens} tokens, give one intuitive, memorable way to '
                            f'remember this in {self.language}: {word}. Use mostly english.'
                        ),
                    },
                ],
                'max_tokens': max_tokens,
            },
        )

        # Track usage
        data = await response.json()
        if response.status != 200:
            raise RuntimeError(f'Error in generate_intuitive_explanation for {word}: {data}')
        tokens = data['usage']['total_tokens']
        self.tokens.append(tokens)

        # GPT-4
        explanation = data['choices'][0]['message']['content'].strip(' \n')

        end = datetime.now()
        logging.debug(
            f'GPT-4o explained {word} as {explanation} in {(end-start).total_seconds()} '
            f'seconds with {tokens} tokens used'
        )

        return explanation


# Utilities
def auto_detect_language(words_to_search: list[str]) -> str:
    """Auto-detect language of the words using GPT-4o"""
    world_languages = get_all_languages_lower()
    # Uses up to `num_words_to_detect` words to auto-detect language
    num_words_to_detect = 10
    chat_completion = client.chat.completions.create(
        messages=[
            {
                'role': 'user',
                'content': (
                    'In a single word, what language are all these words in? '
                    'If you do not know, type "I do not know". '
                    f'Words: {", ".join(words_to_search[:num_words_to_detect])}'
                ),
            }
        ],
        model='gpt-4o',
    )
    language = chat_completion.choices[0].message.content
    language = language.strip(STRIP_CHARACTERS).lower()

    if language not in world_languages:
        raise RuntimeError(
            f'Invalid language detected: {language}. `LANGUAGE` not specified in python/config.py '
            'and GPT is used to autodetect language from provided cards. Check that the input file '
            'contains words in a single language. '
            'Please explicitly specify a valid language by providing `--language` to '
            '`anki_language_gpt`'
        )

    return language


def auto_detect_romanization(language: str) -> bool:
    """Auto-detect if the language needs romanization using GPT-4o"""
    chat_completion = client.chat.completions.create(
        messages=[
            {
                'role': 'user',
                'content': (
                    f'Reply Yes or No, does does "{language}" need romanization for an '
                    'english speaker to pronounce?'
                ),
            }
        ],
        model='gpt-4o',
    )
    use_romanization = chat_completion.choices[0].message.content
    use_romanization = use_romanization.strip(STRIP_CHARACTERS).lower()

    if use_romanization not in ['yes', 'no']:
        raise RuntimeError(
            f'GPT was unable to infer romanization of language {language}. '
            f'GPT responded: {use_romanization}. Please specify `--use-romanization` '
            'to `anki_language_gpt` as `True` or `False`.'
        )

    return use_romanization == 'yes'
