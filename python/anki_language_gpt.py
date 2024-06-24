import asyncio
import csv
import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp
import click
import pandas as pd
from openai_generator import (
    STRIP_CHARACTERS,
    OpenAIGenerator,
    auto_detect_language,
    auto_detect_romanization,
)
from translation_utils import (
    FlashCard,
    OutputFormat,
    OutputGroup,
    SearchResult,
    format_example_sentences,
    get_all_languages_lower,
)


def validate_language(_ctx, _param, value):
    """Validates that the language is a valid ISO-639 language code.

    All valid values can be found in the below ISO 639 code table:
    https://iso639-3.sil.org/code_tables/639/data. Input any valid name from
    the `Language Name(s)` search result.
    """
    languages = get_all_languages_lower()
    if value and value.lower() not in languages:
        raise click.BadParameter(
            'Invalid language. Please refer to the documentation for supported languages.'
        )
    return value


def convert_to_output_format(_ctx, _param, formats: list[str]):
    """Converts the output format string to an OutputFormat enum."""
    try:
        return [OutputFormat(format_str) for format_str in formats]
    except ValueError as e:
        raise ValueError from e(
            f'Invalid output formats: {formats}. All formats must be one of: '
            f'{[v.value for v in OutputFormat]}'
        )


async def search(
    session: aiohttp.ClientSession,
    word: str,
    number_of_sentences: int,
    openai_generator: OpenAIGenerator,
) -> SearchResult:
    """Takes `word` and returns dict of useful GPT augmentations.

    Includes romanization, english definition, example sentences, and intuitive explanation
    of meaning.
    """
    # Use OpenAI to query romanization (Optional)
    romanization = await openai_generator.query_romanization(session, word)
    logging.debug(f'Romanization of ({word}): {romanization}')

    english_translations: list[str] = [await openai_generator.query_translation(session, word)]
    logging.debug(f'Translation ({word}): {english_translations}')

    example_sentences: list[str] = await openai_generator.generate_sample_sentences(
        session, word, number_of_sentences
    )
    logging.debug(f'Sentences ({word}): {example_sentences}')

    explanation = await openai_generator.generate_intuitive_explanation(session, word)
    logging.debug(f'Explanation ({word}): {explanation}')

    logging.info(f'GPT generated card for {word}.')

    english_translation = english_translations[0]
    search_result = SearchResult(
        word, romanization, english_translation, example_sentences, explanation
    )
    return search_result


def generate_csv(output_file: str, flash_cards: list[FlashCard]):
    """Creates a csv (semicolon separated) at `output` from `flash_cards`

    Schema: Target language word, romanization, definition (either english or target language),
    example sentences where example sentences are target language then english, new line separated
    """
    # Write all values as UTF-8 encodings
    with open(output_file, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')

        for card in flash_cards:
            card.write_csv_list(writer)


def generate_xlsx(output_file: str, flash_cards: list[FlashCard]):
    """Creates an xlsx file at `output` from `flash_cards`

    Schema: Target language word, romanization, definition (either english or target language),
    example sentences where example sentences are target language then english, new line separated
    """
    # Create a dataframe from the flashcards
    df = pd.concat([card.to_df_row() for card in flash_cards], ignore_index=True)
    # Write the dataframe to the output file
    df.to_excel(output_file, index=False)


async def runner(
    input_file: str,
    overwrite_output: bool,
    language: Optional[str],
    use_romanization: Optional[bool],
    number_of_sentences: int,
    max_concurrent_cards: int,
    output_groups: list[OutputGroup],
):
    # Timing
    start = datetime.now()

    # Specify "encoding" because UTF-8 encodings (in file) of non english alphabet are not equal
    # to Unicode output which file.read() requires
    words_to_search = []
    unique_words = set()
    with open(input_file, encoding='utf-8') as input_file:
        lines = input_file.readlines()
        # Assumes `input_file` is a list of newline-separated words
        for word in lines:
            # Clean formatting of word (sometimes copies with new lines)
            word = word.strip('\n \t\'"')

            # Skip empty lines
            if not word:
                continue

            # Skip repeated words
            if word in unique_words:
                logging.warning(f'Word {word} is repeated in input file')
                continue

            unique_words.add(word)
            words_to_search.append(word)

    # Optionally auto detect language and romanization
    if language is None:
        logging.info('Auto-detecting language from provided cards')
        language = auto_detect_language(words_to_search)
        use_romanization = auto_detect_romanization(language)
    else:
        world_languages = get_all_languages_lower()
        # Check if user provided language is a valid language
        language = language.strip(STRIP_CHARACTERS).lower()
        if language not in world_languages:
            raise RuntimeError(
                f'Invalid language detected: {language}. '
                'Please specify a valid ISO-639 language via `--language` '
                'to `anki_language_gpt`'
            )

    logging.info(f'Language: {language}')
    logging.info(f'Use Romanization: {use_romanization}')

    logging.debug(f'Will search these words: {words_to_search}')

    openai_generator = OpenAIGenerator(language)
    headers = {'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY")}'}
    semaphore = asyncio.Semaphore(max_concurrent_cards)
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []

        async def sem_task(word):
            async with semaphore:
                return await search(session, word, number_of_sentences, openai_generator)

        for word in words_to_search:
            result = None
            tasks.append(sem_task(word))

        results: list[SearchResult] = await asyncio.gather(*tasks)

    # Write to all outputs
    for output_group in output_groups:
        flash_cards: list[FlashCard] = []
        output_file = output_group.output_file
        output_format = output_group.output_format

        # Clear output file
        if overwrite_output:
            with open(output_file, 'w', newline='', encoding='utf-8') as _:
                pass

        for result in results:
            word = result.foreign_lang_word
            logging.debug(f'Creating FlashCard for: {word}, Output format: {output_format}')
            formatted_result = format_example_sentences(result, output_format)
            # Get definition for particular card types
            card = FlashCard(
                foreign_lang_word=word,
                search_result=formatted_result,
                use_romanization=use_romanization,
                output_format=output_format,
            )
            flash_cards.append(card)

        logging.debug(
            f'Output format: {output_format}, '
            f'Writing flashcards (only 1 cards output): {flash_cards[0]}'
        )
        if output_format in [OutputFormat.ANKI, OutputFormat.SHEETS]:
            generate_csv(output_file, flash_cards)
        else:
            generate_xlsx(output_file, flash_cards)

    end = datetime.now()
    logging.info(
        f'Running time: {int((end-start).total_seconds())} sec to create {len(flash_cards)} '
        'flashcard(s)'
    )
    logging.info(f'Total OpenAI tokens used: {sum(openai_generator.tokens)}')


@click.command()
@click.option(
    '-i',
    '--input-file',
    default='cards/words_to_translate.txt',
    help='File with newline separated words to augment with GPT.',
)
@click.option(
    '-o',
    '--output-file',
    'output_files',
    default=[
        'cards/output_flashcards_anki.csv',
        'cards/output_flashcards_google_sheets.csv',
        'cards/output_flashcards_excel.xlsx',
    ],
    multiple=True,
    help='Output spreadsheet for flashcards.',
)
@click.option(
    '--overwrite-output', is_flag=True, help='Overwrite existing output file (appends by default)'
)
# The foreign language to generate flashcards for. When `None`, GPT is used to autodetect
# the language of the cards in the input file. If GPT cannot auto detect the language,
# then this value must be set to a language name.
@click.option(
    '--language',
    default=None,
    help=(
        'Language of the input words, e.g. chinese, arabic, french '
        '(any valid ISO-639 language name). '
        'If not provided, then GPT auto detects the language from the input words.'
    ),
    callback=validate_language,
    type=str,
)
# Set to `True` for logographic languages (like Chinese, Japanese, etc) which have
# romanization (pinyin, romaji) that can be generated to pronounce the word. Otherwise,
# set to `False` for languages like German, Spanish, etc.
#
# When `None`, GPT is used to deduce whether the language is logographic and needs romanization.
# If GPT cannot auto detect the romanization, then this value must be set to `True` or `False`.
@click.option(
    '--use-romanization',
    default=None,
    help=(
        'Whether the language needs romanization. '
        'If not provided, then GPT auto detects romanization from the input words.'
    ),
    type=bool,
)
@click.option(
    '--number-of-sentences',
    default=3,
    help='Number of example sentences to generate per card',
    type=int,
)
# Maximum number of concurrent cards to generate.
# As of this writing, GPT-4o has a limit of 500 requests per min (RPM) and 30k tokens
# per minute (TPM) for Tier 1 users.
# https://platform.openai.com/docs/guides/rate-limits/usage-tiers?context=tier-one
# Tier 2+ users will not get close to their 5000+ RPM limit with this script.
# Adjust this limit to avoid hitting the RPM + TPM limits.
# This default set to 100 because the script makes 4 API requests per word and
# takes ~400 tokens per request.
# Most users will not have 100 cards to generate so they can use their RPM quota
# all in "one burst".
#
# Although if you have more you are one dedicated language learner :)
@click.option(
    '--max-concurrent-cards',
    default=100,
    help='Maximum number of cards to generate concurrently.',
    type=int,
)
# Output format for the cards spreadsheet
# Anki:
# - Bold target word: Uses `<b>**{word}**<b/>`
# - New line: Uses `\n`
# - Separator: Uses `;`
# Sheets:
# - Bold target word: Uses `**{word}**`
# - New line: Uses `\n`
# - Separator: Uses `;`
# Excel:
# - Bold target word: Uses `**{word}**`
# - New line: Uses grouped "", regular '"' is converted to double '""'
# - Separator: Uses `;`
@click.option(
    '--output-format',
    'output_formats',
    multiple=True,
    default=['anki', 'sheets', 'excel'],
    help=(
        'Customizes the output file format for an output source ("excel", "sheets", "anki"). '
        'Excel output file must end in .xlsx and anki/sheets must end in .csv.'
    ),
    callback=convert_to_output_format,
    type=click.Choice(['excel', 'sheets', 'anki']),
)
@click.option(
    '--log-level',
    default='INFO',
    help='Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)',
    type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']),
)
def main(
    input_file: str,
    output_files: list[str],
    overwrite_output: bool,
    language: Optional[str],
    use_romanization: Optional[bool],
    number_of_sentences: int,
    max_concurrent_cards: int,
    output_formats: list[OutputFormat],
    log_level: str,
):
    """
    Takes words from a foreign language and generates supplementary info
    (english translation, sample sentences, etc.) in a format that can be imported
    into Anki as flashcards, or as a spreadsheet for Google Sheets or Excel.
    """
    # Creative way to get logging level: logging.LEVEL is a class var and constant int,
    # retrive that constant
    log_level = getattr(logging, log_level)
    logging.basicConfig(format='%(asctime)s  [%(levelname)s] %(message)s', level=log_level)

    logging.info('Starting Anki Language GPT')
    logging.info(f'Input file: {input_file}')
    logging.info(f'Output file(s): {output_files}')
    logging.info(f'Overwrite output: {overwrite_output}')
    logging.info(f'Max concurrent cards: {max_concurrent_cards}')
    logging.info(f'Output format: {output_formats}')

    output_groups = [
        OutputGroup(output_file, output_format)
        for output_file, output_format in zip(output_files, output_formats)
    ]

    for output_group in output_groups:
        output_file = output_group.output_file
        output_format = output_group.output_format
        # Validate: If output format is Excel, then output file must end in .xlsx
        if output_format == OutputFormat.EXCEL and not output_file.endswith('.xlsx'):
            raise ValueError(
                f'Output file ({output_file}) must end in .xlsx for Excel output format'
            )
        # If output format is Anki or Sheets, then output file must end in .csv
        if output_format in [OutputFormat.ANKI, OutputFormat.SHEETS] and not output_file.endswith(
            '.csv'
        ):
            raise ValueError(
                f'Output file ({output_file}) must end in .csv for Anki/Sheets output format'
            )

    asyncio.run(
        runner(
            input_file,
            overwrite_output,
            language,
            use_romanization,
            number_of_sentences,
            max_concurrent_cards,
            output_groups,
        )
    )


if __name__ == '__main__':
    main()
