import os
import site
import email
import logging
import pytest
from unittest import mock

from bs4 import BeautifulSoup

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from src.services import send_mail

# 로깅 설정
logger = logging.getLogger(__file__)

# 단위테스트 실행 커멘드
# pytest -vs tests/test_send_mail.py
# 실제 SMTP 접속은 일어나지 않는다(send_mail.mail_server_login 을 mock 으로 대체).

# HTML 에서 특수한 의미를 갖는 문자가 모두 들어 있는 값
NASTY = '<b>a<b & "q" \'s\' </div>'
# 줄 맨 앞에 '*' 나 공백이 오면 코드가 벗겨내므로, 요약 줄은 일반 문자로 시작시킨다
NASTY_LINE = 'x<think>a<b & "q" \'s\' </div>'


def send_and_get_html(news_data):
    """
    send_email_with_news 를 SMTP 없이 실행하고, mock 서버의 sendmail 에 넘어간 MIME 문자열에서
    HTML 본문을 꺼내서 반환한다.
    """
    server = mock.MagicMock()
    with mock.patch.object(send_mail, 'mail_server_login', return_value=server) as login:
        send_mail.send_email_with_news('sender@example.com', 'pwd',
                                       ['a@example.com', 'b@example.com'], news_data)
    login.assert_called_once()
    server.sendmail.assert_called_once()
    mime_str = server.sendmail.call_args[0][2]
    msg = email.message_from_string(mime_str)
    return msg.get_payload(decode=True).decode('utf-8')


def make_news(**overrides):
    item = {
        'title': '평범한 제목',
        'date': '2024-05-03',
        'url': 'https://example.com/news/1',
        'summary': '* 첫째 줄\n* 둘째 줄',
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------- escape_html

def test_escape_html_special_chars():
    assert send_mail.escape_html('<a href="x">&\'') == '&lt;a href=&quot;x&quot;&gt;&amp;&#x27;'


def test_escape_html_non_string_values():
    assert send_mail.escape_html(None) == 'None'
    assert send_mail.escape_html(123) == '123'


def test_escape_html_plain_text_unchanged():
    assert send_mail.escape_html('평범한 text 123') == '평범한 text 123'


# ------------------------------------------------------- send_email_with_news

def test_send_email_does_not_touch_real_smtp():
    """mail_server_login 을 막아도 smtplib 으로 접속하지 않는지 방어적으로 확인한다."""
    with mock.patch('smtplib.SMTP', side_effect=AssertionError('real SMTP used')):
        html = send_and_get_html([make_news()])
    assert '평범한 제목' in html


def test_news_roundtrip_special_chars():
    """특수문자가 든 값이 파싱 후 원래 문자열 그대로 복원되고, 기사 블록 수가 유지된다."""
    items = [
        make_news(title=NASTY + '1', date=NASTY + 'd1', url='https://e.com/?a="1"&b=<2>',
                  summary=f'* {NASTY_LINE}1\n* {NASTY_LINE}2'),
        make_news(title=NASTY + '2', date=NASTY + 'd2', url="https://e.com/'x'?y=1&z=2",
                  summary=f'{NASTY_LINE}3'),
    ]
    soup = BeautifulSoup(send_and_get_html(items), 'html.parser')

    blocks = soup.select('div.news-item')
    assert len(blocks) == len(items)
    for block, item in zip(blocks, items):
        assert block.select_one('h2').get_text() == item['title']
        assert block.select_one('p.date').get_text() == item['date']
        links = block.select('a.url')
        assert len(links) == 1
        assert links[0]['href'] == item['url']
        expected_lines = [l.strip().lstrip('* ').strip() for l in item['summary'].split('\n') if l.strip()]
        assert [li.get_text() for li in block.select('li')] == expected_lines


def test_news_injection_has_no_script_element():
    payload = '<script>alert(1)</script></div>'
    items = [make_news(title=payload, date=payload, url=payload, summary=f'* {payload}\n* z{payload}')]
    soup = BeautifulSoup(send_and_get_html(items), 'html.parser')

    assert soup.find('script') is None
    assert len(soup.select('div.news-item')) == 1
    assert soup.select_one('h2').get_text() == payload
    assert soup.select_one('a.url')['href'] == payload
    # 푸터가 여전히 container 안쪽(구조 유지)에 있어야 한다
    assert soup.select_one('div.container div.footer') is not None


def test_news_href_attribute_escape_blocked():
    url = 'https://e.com/" onclick="alert(1)" data-x="'
    soup = BeautifulSoup(send_and_get_html([make_news(url=url)]), 'html.parser')
    a = soup.select_one('a.url')
    assert a['href'] == url
    assert set(a.attrs.keys()) == {'href', 'class', 'target', 'rel'}
    assert a.get('onclick') is None


def test_news_url_with_ampersand_roundtrip():
    url = 'https://example.com/a?x=1&y=2'
    soup = BeautifulSoup(send_and_get_html([make_news(url=url)]), 'html.parser')
    assert soup.select_one('a.url')['href'] == url


def test_news_li_is_real_li_elements():
    """코드가 만든 <ul><li> 구조는 이스케이프되지 않고 실제 요소로 남아야 한다."""
    html = send_and_get_html([make_news(summary='* 하나\n\n* 둘\n* 셋')])
    soup = BeautifulSoup(html, 'html.parser')
    assert [li.get_text() for li in soup.select('div.summary ul > li')] == ['하나', '둘', '셋']
    assert '&lt;li&gt;' not in html and '&lt;ul&gt;' not in html


def test_news_plain_input_unchanged():
    soup = BeautifulSoup(send_and_get_html([make_news()]), 'html.parser')
    assert soup.select_one('h2').get_text() == '평범한 제목'
    assert soup.select_one('p.date').get_text() == '2024-05-03'
    assert soup.select_one('a.url')['href'] == 'https://example.com/news/1'
    assert [li.get_text() for li in soup.select('li')] == ['첫째 줄', '둘째 줄']


def test_news_without_url_has_no_link():
    item = make_news()
    del item['url']
    soup = BeautifulSoup(send_and_get_html([item]), 'html.parser')
    assert soup.select('a.url') == []
    assert len(soup.select('div.news-item')) == 1


def test_news_date_none_does_not_raise():
    soup = BeautifulSoup(send_and_get_html([make_news(date=None)]), 'html.parser')
    assert soup.select_one('p.date').get_text() == 'None'


def test_news_multiple_items_block_count():
    items = [make_news(title=f'<t{i}>', summary=f'* a<b{i}') for i in range(5)]
    soup = BeautifulSoup(send_and_get_html(items), 'html.parser')
    assert len(soup.select('div.news-item')) == 5
    assert [h.get_text() for h in soup.select('h2')] == [f'<t{i}>' for i in range(5)]
