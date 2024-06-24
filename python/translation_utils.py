from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

import pycountry
import pandas as pd


class OutputFormat(Enum):
    """Enum for the output format of the flashcards."""

    SHEETS = 'sheets'
    ANKI = 'anki'
    EXCEL = 'excel'

    def __repr__(self):
        return self.value


@dataclass
class OutputGroup:
    """An OutputFormat and output file."""

    output_file: str
    output_format: OutputFormat


@dataclass
class ExampleSentence:
    """A sentence in the target language and its English translation."""

    foreign_lang: str
    english: str


@dataclass
class SearchResult:
    """GPT augmented row in the output file."""

    foreign_lang_word: str
    romanization: Optional[str]
    english_def: str
    example_sentences: list[ExampleSentence]
    explanation: str


def format_example_sentences(
    search_result: SearchResult, output_format: OutputFormat
) -> SearchResult:
    """Return formatted example sentences based on the output format."""
    example_sentences = search_result.example_sentences
    foreign_lang_word = search_result.foreign_lang_word
    formatted_example_sentences: list[ExampleSentence] = []
    for sentence in example_sentences:
        # Bold the word in the example sentence based on output format
        if output_format in [OutputFormat.EXCEL, OutputFormat.SHEETS]:
            foreign_lang_example = sentence.foreign_lang.replace(
                foreign_lang_word, f'**{foreign_lang_word}**'
            )
        else:
            assert output_format == OutputFormat.ANKI
            foreign_lang_example = sentence.foreign_lang.replace(
                foreign_lang_word, f'<b>**{foreign_lang_word}**</b>'
            )

        # Replace the `"` with `""` for Excel
        # then group the example sentences in `""`
        if output_format == OutputFormat.EXCEL:
            foreign_lang_example = foreign_lang_example.replace('"', '""')
            foreign_lang_example = f'"{foreign_lang_example}"'

        formatted_example = replace(sentence, foreign_lang=foreign_lang_example)
        formatted_example_sentences.append(formatted_example)

    search_result = replace(search_result, example_sentences=formatted_example_sentences)
    return search_result


@dataclass
class FlashCard:
    """Anki Flashcard"""

    foreign_lang_word: str
    search_result: SearchResult
    use_romanization: bool
    output_format: OutputFormat

    def to_df_row(self) -> pd.DataFrame:
        """Returns a DataFrame row for the flashcard."""
        # This should only be called for `OutputFormat.EXCEL`
        if self.output_format != OutputFormat.EXCEL:
            raise RuntimeError('`to_df_row` should only be called for Excel output format.')

        # Sentence translation pairs are new line separated, ordered foreign language then English
        # Different example sentences are later further new line separated
        if self.foreign_lang_word != self.search_result.foreign_lang_word:
            raise RuntimeError(
                f'Input word {self.foreign_lang_word} and search result word '
                f'{self.search_result.foreign_lang_word} do not match'
            )

        # Excel takes new lines as `\n`
        new_line = '\n'

        sentences = [
            f'{s.foreign_lang}{new_line}{s.english}' for s in self.search_result.example_sentences
        ]
        # Default: write sentences as new line separated
        if self.use_romanization:
            return pd.DataFrame(
                {
                    'Word': [self.foreign_lang_word],
                    'Romanization': [self.search_result.romanization],
                    'Translation': [self.search_result.english_def],
                    'Example Sentences': [f'{new_line}{new_line}'.join(sentences)],
                    'Explanation': [self.search_result.explanation],
                }
            )
        else:
            return pd.DataFrame(
                {
                    'Word': [self.foreign_lang_word],
                    'Translation': [self.search_result.english_def],
                    'Example Sentences': [f'{new_line}{new_line}'.join(sentences)],
                    'Explanation': [self.search_result.explanation],
                }
            )

    def write_csv_list(self, writer):
        """Returns a list of fields formatted for writing to csv, writer is a csv.writer"""
        # This should only be called for `OutputFormat.SHEETS` or `OutputFormat.ANKI`
        if self.output_format not in {OutputFormat.SHEETS, OutputFormat.ANKI}:
            raise RuntimeError(
                '`write_csv_list` should only be called for Sheets or Anki output format.'
            )

        # Sentence translation pairs are new line separated, ordered foreign language then English
        # Different example sentences are later further new line separated
        if self.foreign_lang_word != self.search_result.foreign_lang_word:
            raise RuntimeError(
                f'Input word {self.foreign_lang_word} and search result word '
                f'{self.search_result.foreign_lang_word} do not match'
            )

        # Anki takes HTML new lines as `<br>`. The `\n` is useful if users want to copy the
        # Anki output format into Google sheets, sheets will detect the `\n`. In this case,
        # note that the `<br>` will be uninterpreted by sheets though.
        new_line = '\n<br>' if self.output_format == OutputFormat.ANKI else '\n'

        sentences = [
            f'{s.foreign_lang}{new_line}{s.english}' for s in self.search_result.example_sentences
        ]
        # Default: write sentences as new line separated
        if self.use_romanization:
            writer.writerow(
                [
                    self.foreign_lang_word,
                    self.search_result.romanization,
                    self.search_result.english_def,
                    f'{new_line}{new_line}'.join(sentences),
                    self.search_result.explanation,
                ]
            )
        else:
            writer.writerow(
                [
                    self.foreign_lang_word,
                    self.search_result.english_def,
                    f'{new_line}{new_line}'.join(sentences),
                    self.search_result.explanation,
                ]
            )


def get_all_languages_lower() -> set[str]:
    """Returns a list of all languages (accordance with ISO 639) lower case.

    All valid values can be found in the below ISO 639 code table:
    https://iso639-3.sil.org/code_tables/639/data. Input any valid name from
    the `Language Name(s)` search result.
    """
    languages = {lang.name.lower() for lang in pycountry.languages}
    return languages
