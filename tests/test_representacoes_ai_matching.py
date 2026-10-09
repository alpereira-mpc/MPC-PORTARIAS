"""Safe matching for names extracted from Representação PDFs."""

from services.representacoes_ai import match_people, match_person, match_relator


def test_match_person_ignores_accents_particles_case_and_punctuation():
    choices = {10: "João da Silva Nogueira", 20: "Maria dos Santos Vieira"}
    assert match_person(" JOAO  SILVA-NOGUEIRA ", choices) == 10


def test_match_person_accepts_only_unique_minor_full_name_variants():
    choices = {
        10: "Alanna Camilla Santos Galdino Vieira",
        20: "Antônio Gomes Vieira Filho",
    }
    assert match_person("Alana Camila Santos Galdino Vieira", choices) == 10
    assert match_person("Antonio Gomes Vieira", choices) is None
    assert match_person("Antonio G Vieira Filho", choices) is None
    assert match_person("Ana Silva", {10: "Anna Silva"}) is None


def test_match_person_rejects_ambiguous_name_even_with_exact_normalization():
    choices = {10: "José de Souza Filho", 20: "Jose Souza Filho"}
    assert match_person("Jose Souza Filho", choices) is None
    assert match_person("Joao Souza Filho", choices) is None


def test_match_people_deduplicates_and_skips_uncertain_names():
    choices = {10: "João da Silva Nogueira", 20: "Maria dos Santos Vieira"}
    assert match_people(
        [
            "Maria Santos Vieira",
            "Joao Silva Nogueira",
            "Maria dos Santos Vieira",
            "Pessoa desconhecida",
        ],
        choices,
    ) == [20, 10]


def test_match_relator_returns_only_a_valid_unique_choice():
    choices = (
        "André Carlo Torres Pontes",
        "Antônio Gomes Vieira Filho",
    )
    assert match_relator("Andre Carlo Torres Pontes", choices) == choices[0]
    assert match_relator("Andre Carlo Pontes", choices) is None
    assert match_relator("Conselheiro André Carlo Torres Pontes", choices) is None
