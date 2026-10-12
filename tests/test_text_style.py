import pytest

from utils.text_style import count_sentences


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", 0),
        ("3.5kg 덤벨로 해요", 1),                                    # decimals don't split
        ("좋아요 9시 등 운동!", 1),
        ("짠, 운완이가 먼저 인사해봐요 👋 늦었으니 오늘은 푹 자요, 내일 봐요!", 2),  # emoji as a break
        ("ㅎㅇㅎㅇ 반가워요 doodle0님! 저는 운동 친구 운완이에요 😊 늦었으니 오늘은 푹 자요!", 3),
        ("좋아요, 9시 등 운동 시작이네요! 무릎은 좀 어때요? 아프면 쉬어도 괜찮아요. 계속 아프면 병원 가봐요.", 4),
    ],
)
def test_count_sentences(text, expected):
    assert count_sentences(text) == expected
