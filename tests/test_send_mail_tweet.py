import os
import site
import logging
import pytest

from bs4 import BeautifulSoup

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from src.services import send_mail_tweet

# 로깅 설정
logger = logging.getLogger(__file__)

# 단위테스트 실행 커멘드
# pytest -vs tests/test_send_mail_tweet.py
# create_email_body 는 순수 함수라 네트워크·파일·config.json 이 필요 없다.

# HTML 에서 특수한 의미를 갖는 문자가 모두 들어 있는 값 (앞뒤 공백 없음)
NASTY = 'a<b & "q" \'s\' <think> </div>'
ISO = '2024-05-03T10:20:30Z'
ISO_DATE = '24.5.3'


def make_post(**overrides):
    post = {
        'url': 'https://example.com/status/1',
        'created_at': ISO,
        'text': '평범한 원문 텍스트입니다 정말로',
        'title': '평범한 제목',
        'summary': '* 첫째\n* 둘째',
        'translated_text': None,
    }
    post.update(overrides)
    return post


def parse(posts):
    html = send_mail_tweet.create_email_body(posts)
    return BeautifulSoup(html, 'html.parser')


def original_of(post_div):
    """'원문:' 레이블 뒤의 텍스트를 복원한다."""
    div = post_div.select_one('div.post-text')
    strong = div.find('strong')
    assert strong.get_text() == '원문:'
    text = ''.join(
        str(s) for s in strong.next_siblings if isinstance(s, str)
    )
    assert text.startswith(' ')
    return text[1:]


def summary_text_of(post_div):
    """요약 블록에서 '요약:' 레이블과 첫 <br> 이후를 복원한다. 실제 <br> 요소는 \\n 으로 바꾼다."""
    div = post_div.select_one('div.post-summary')
    parts = []
    seen_label_br = False
    for child in div.contents:
        if getattr(child, 'name', None) == 'strong':
            continue
        if getattr(child, 'name', None) == 'br':
            if not seen_label_br:
                seen_label_br = True
                continue
            parts.append('\n')
        elif getattr(child, 'name', None) is None:
            parts.append(str(child))
    return ''.join(parts).strip()


# ------------------------------------------------------------- 왕복 검증

def test_roundtrip_all_fields_bullet_summary():
    posts = [
        make_post(
            url='https://e.com/?a="1"&b=<2>',
            created_at=ISO,
            text='원문 ' + NASTY + ' 1',
            title='제목 ' + NASTY,
            summary=f'* 항목1 {NASTY}\n* 항목2 <script>alert(1)</script>\n* 항목3 &amp; 그대로',
        ),
        make_post(
            url="https://e.com/'x'?y=1&z=2",
            text='원문 ' + NASTY + ' 2',
            title='<h1>제목2</h1>',
            summary='* 단일 항목 a<b',
        ),
    ]
    soup = parse(posts)

    blocks = soup.select('div.post')
    assert len(blocks) == len(posts)
    for block, post in zip(blocks, posts):
        assert original_of(block) == post['text']
        assert block.select_one('h3.post-title').get_text() == f"{post['title']} ({ISO_DATE})"
        expected_items = [i.strip().lstrip('*').strip() for i in post['summary'].strip().split('\n*')]
        assert [li.get_text() for li in block.select('div.post-summary ul > li')] == expected_items
        link = block.select_one('a.post-link')
        assert link['href'] == post['url']
        assert link.get_text() == post['url']
        assert block.select_one('div.post-header').get_text() == f"게시일시: {post['created_at']}"


def test_roundtrip_non_bullet_summary():
    summary = f'첫줄 {NASTY}\n둘째줄 x<br>y\n셋째줄'
    soup = parse([make_post(summary=summary)])
    block = soup.select_one('div.post')
    assert summary_text_of(block) == summary
    # 코드가 만든 줄바꿈은 실제 <br> 요소이고, 줄바꿈이 2개이므로 요약 블록 안 <br> 은 레이블용 1개 + 2개
    assert len(block.select('div.post-summary br')) == 3
    assert block.select('div.post-summary ul') == []


def test_non_bullet_summary_br_is_real_element_not_text():
    html = send_mail_tweet.create_email_body([make_post(summary='줄1\n줄2')])
    assert '&lt;br&gt;' not in html
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('div.post-summary br')) == 2  # 레이블 뒤 1개 + 줄바꿈 1개


def test_bullet_summary_is_real_ul_li():
    html = send_mail_tweet.create_email_body([make_post(summary='* 하나\n* 둘\n* 셋')])
    assert '&lt;ul&gt;' not in html and '&lt;li&gt;' not in html
    soup = BeautifulSoup(html, 'html.parser')
    assert [li.get_text() for li in soup.select('div.post-summary ul > li')] == ['하나', '둘', '셋']


def test_header_with_non_iso_created_at_roundtrip():
    created = '<b>2024 & "x"'
    soup = parse([make_post(created_at=created)])
    assert soup.select_one('div.post-header').get_text() == f'게시일시: {created}'
    # 날짜 파싱에 실패하면 제목 뒤 날짜는 비어 있다
    assert soup.select_one('h3.post-title').get_text() == '평범한 제목 ()'
    assert len(soup.select('div.post')) == 1


def test_iso_created_at_gives_date_in_title():
    soup = parse([make_post(created_at='2024-12-25T00:00:00+00:00')])
    assert soup.select_one('h3.post-title').get_text() == '평범한 제목 (24.12.25)'
    soup = parse([make_post(created_at=ISO)])
    assert soup.select_one('h3.post-title').get_text() == f'평범한 제목 ({ISO_DATE})'


# ---------------------------------------------------------- 번역문 전용

def test_translated_only_roundtrip():
    translated = '번역 a<b & "q"\n둘째줄 <script>alert(1)</script> 끝'
    soup = parse([make_post(summary=None, title=None, translated_text=translated)])
    block = soup.select_one('div.post')
    divs = block.select('div.post-text')
    assert len(divs) == 2  # 원문 + 번역문
    trans = divs[1]
    assert trans.find('strong').get_text() == '번역문:'
    assert trans.get_text().replace('번역문:', '', 1).strip() == translated.replace('\n', '') + f' ({ISO_DATE})'
    assert soup.find('script') is None
    assert block.select('div.post-summary') == []


def test_translated_literal_br_is_shown_as_text():
    """번역문에 든 리터럴 <br> 은 이제 태그가 아니라 글자로 보이는 것이 의도된 동작이다."""
    html = send_mail_tweet.create_email_body(
        [make_post(summary=None, title=None, translated_text='앞<br>뒤\n끝')])
    soup = BeautifulSoup(html, 'html.parser')
    trans = soup.select('div.post-text')[1]
    assert trans.get_text().replace('번역문:', '', 1).strip() == f'앞<br>뒤끝 ({ISO_DATE})'
    # 번역문 블록의 <br> 요소는 레이블 뒤 1개뿐
    assert len(trans.select('br')) == 1


def test_summary_takes_priority_over_translation():
    soup = parse([make_post(translated_text='번역문 내용')])
    block = soup.select_one('div.post')
    assert len(block.select('div.post-text')) == 1
    assert block.select_one('div.post-summary') is not None


# ------------------------------------------------------------ 주입 방어

def test_script_injection_creates_no_script_element():
    payload = '<script>alert(1)</script></div></div>'
    posts = [
        make_post(url=payload, created_at=payload, text='원문 ' + payload, title=payload,
                  summary=f'* {payload}\n* x{payload}'),
        make_post(summary=f'비불릿 {payload}'),
        make_post(summary=None, title=None, translated_text=payload),
    ]
    soup = parse(posts)
    assert soup.find('script') is None
    assert len(soup.select('div.post')) == 3
    # </div> 주입으로 container 밖으로 구조가 깨지지 않았는지
    assert len(soup.select('div.container > div.post')) == 3


def test_href_attribute_escape_blocked():
    url = 'https://e.com/" onclick="alert(1)" data-x="'
    soup = parse([make_post(url=url)])
    a = soup.select_one('a.post-link')
    assert a['href'] == url
    assert set(a.attrs.keys()) == {'href', 'class'}
    assert a.get('onclick') is None
    assert a.get_text() == url


def test_url_with_ampersand_roundtrip():
    url = 'https://example.com/a?x=1&y=2'
    a = parse([make_post(url=url)]).select_one('a.post-link')
    assert a['href'] == url
    assert a.get_text() == url


# ------------------------------------------- 길이 검사는 원래 값 기준

def test_short_text_is_excluded_by_original_length():
    # 13자: 이스케이프하면 '&lt;' 로 길어져 15자를 넘지만, 원래 값 기준이라 제외돼야 한다
    short = '<' * 13
    assert len(short) < 15
    soup = parse([make_post(text=short), make_post(text='충분히 길고 평범한 원문 텍스트입니다')])
    blocks = soup.select('div.post')
    assert len(blocks) == 1
    assert original_of(blocks[0]) == '충분히 길고 평범한 원문 텍스트입니다'


def test_length_boundary_14_excluded_15_included():
    t14, t15 = '<' * 14, '<' * 15
    soup = parse([make_post(text=t14), make_post(text=t15)])
    blocks = soup.select('div.post')
    assert len(blocks) == 1
    assert original_of(blocks[0]) == t15


# ------------------------------------------------------------ 평범한 입력

def test_plain_input_text_unchanged():
    post = make_post()
    html = send_mail_tweet.create_email_body([post])
    soup = BeautifulSoup(html, 'html.parser')
    block = soup.select_one('div.post')
    assert original_of(block) == post['text']
    assert block.select_one('h3.post-title').get_text() == f"평범한 제목 ({ISO_DATE})"
    assert [li.get_text() for li in block.select('li')] == ['첫째', '둘째']
    assert block.select_one('a.post-link')['href'] == post['url']
    assert '&amp;' not in html.split('<body>')[1] and '&lt;' not in html.split('<body>')[1]


def test_empty_posts_returns_notice():
    assert send_mail_tweet.create_email_body([]) == "<p>표시할 게시물 데이터가 없습니다.</p>"


def test_missing_optional_fields_use_defaults():
    # url, created_at 이 없으면 기본값('#', '날짜 정보 없음')이 쓰이고 예외가 없어야 한다
    soup = parse([{'text': '충분히 긴 원문 텍스트 a<b 입니다', 'translated_text': '번역 <i>'}])
    block = soup.select_one('div.post')
    assert block.select_one('div.post-header').get_text() == '게시일시: 날짜 정보 없음'
    assert block.select_one('a.post-link')['href'] == '#'
    assert original_of(block) == '충분히 긴 원문 텍스트 a<b 입니다'


def test_block_count_matches_input_with_many_special_posts():
    posts = [make_post(text=f'원문 <{i}> & "q" 충분히 긴 텍스트', title=f'<h{i}>') for i in range(6)]
    soup = parse(posts)
    assert len(soup.select('div.post')) == 6
    assert [h.get_text() for h in soup.select('h3.post-title')] == [f'<h{i}> ({ISO_DATE})' for i in range(6)]
