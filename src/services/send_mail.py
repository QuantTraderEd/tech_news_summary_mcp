import os
import sys
import logging
import traceback
import json
import smtplib

from email import encoders
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

def load_recv_emails_from_config():
    """
    JSON 파일 읽고 이메일 리스트 로딩
    :raises ValueError: receiver_email_list 항목이 리스트가 아닙니다.
    :return: receiver_email_list
    """
    config_path = os.path.join(pjt_home_path, 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            receiver_email_list = config.get('RECEIVER_EMAIL_LIST', [])
            if not isinstance(receiver_email_list, list):
                raise ValueError("receiver_email_list 항목이 리스트가 아닙니다.")
            return receiver_email_list
    except FileNotFoundError:
        logger.error(f"파일을 찾을 수 없습니다: {config_path}")
        return []
    except json.JSONDecodeError:
        logger.error("JSON 파일 형식이 잘못되었습니다.")
        return []

def mail_server_login(mail_accnt='', pwd=''):
    """
    mail_server_login
    :param mail_accnt: ex: login@naver.com
    :param pwd:  ex: pwd
    :return: mail_server
    """
    mail_server = smtplib.SMTP('smtp.naver.com', 587)
    mail_server.ehlo()
    mail_server.starttls()
    mail_server.ehlo()
    # mail_server.starttls() # 이 줄은 중복되므로 주석 처리하거나 제거하는 것이 좋습니다.

    mail_server.login(mail_accnt, pwd)

    return mail_server

def send_mail(mail_accnt: str, pwd: str, to_mail_list: list,
              mail_title: str, mail_text: str):

    mail_server = mail_server_login(mail_accnt, pwd)

    # 제목, 본문 작성
    msg = MIMEMultipart()
    msg['From'] = mail_accnt
    msg['To'] = ', '.join(to_mail_list)
    msg['Subject'] = mail_title
    msg.attach(MIMEText(mail_text, 'plain'))

    # 파일첨부 (파일 미첨부시 생략가능)
    # attachment = open('./data/%s' % target_filename, 'rb')
    # part = MIMEBase('application', 'octet-stream')
    # part.set_payload(attachment.read())
    # encoders.encode_base64(part)
    # filename = os.path.basename('./data/%s' % target_filename)
    # part.add_header('Content-Disposition', "attachment; filename= " + filename)
    # msg.attach(part)

    # 메일 전송
    mail_server.sendmail(mail_accnt, to_mail_list, msg.as_string())
    logger.info(f"send mail: {mail_title}")

    mail_server.quit()

def send_email_with_news(sender_email: str, sender_password: str, receiver_email_list: list, news_data: list):
    """
    뉴스 데이터를 포함한 이메일을 발송합니다.

    Args:
        sender_email (str): 발신자 이메일 주소.
        sender_password (str): 발신자 이메일 비밀번호 (또는 앱 비밀번호).
        receiver_email_list (list): 수신자 이메일 주소 리스트.
        news_data (list): 뉴스 기사 딕셔너리 리스트.
    """

    # 이메일 본문 HTML 생성 (모바일 가독성 고려)
    html_body = """
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Inter', sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 20px; background-color: #f4f4f4; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0, 0, 0, 0.1); }}
            h1 {{ color: #0056b3; text-align: center; margin-bottom: 20px; }}
            .news-item {{ border-bottom: 1px solid #eee; padding-bottom: 15px; margin-bottom: 15px; }}
            .news-item:last-child {{ border-bottom: none; margin-bottom: 0; padding-bottom: 0; }}
            h2 {{ color: #007bff; font-size: 1.2em; margin-top: 0; margin-bottom: 5px; }}
            p {{ margin-bottom: 5px; }}
            .date {{ font-size: 0.9em; color: #666; }}
            .url {{ font-size: 0.9em; color: #007bff; text-decoration: none; display: block; margin-top: 5px; }}
            .summary {{ font-size: 1em; color: #555; }}
            ul {{ list-style: none; padding: 0; margin: 0; }}
            li {{ margin-bottom: 5px; }}
            .footer {{ text-align: center; font-size: 0.8em; color: #999; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>최신 테크 뉴스 요약</h1>
    """

    for news_item in news_data:
        html_body += f"""
            <div class="news-item">
                <h2>{news_item['title']}</h2>
                <p class="date">{news_item['date']}</p>                
        """
        
        # URL이 존재하는 경우에만 추가
        if 'url' in news_item and news_item['url']:
            html_body += f"""
                <a href="{news_item['url']}" class="url" target="_blank" rel="noopener noreferrer">원문 보기</a>
            """
        
        # 요약 내용을 줄바꿈 기준으로 리스트 아이템으로 변환하고, 선행하는 '*'와 공백을 제거
        html_body += f"""
                <div class="summary">
                    <ul>
        """
        for line in news_item['summary'].split('\n'):
            if line.strip(): # 빈 줄은 제외
                # Remove leading asterisk and any space after it
                cleaned_line = line.strip().lstrip('* ').strip()
                html_body += f"<li>{cleaned_line}</li>"
        html_body += """
                    </ul>
                </div>
            </div>
        """

    html_body += """
            <div class="footer">
                <p>이 이메일은 자동 발송되었습니다.</p>
            </div>
        </div>
    </body>
    </html>
    """
        
    mail_accnt = sender_email
    pwd = sender_password
    to_mail_list = receiver_email_list
    logger.debug(f"발신자: {mail_accnt}, 수신자: {to_mail_list}")
    

    mail_server = mail_server_login(mail_accnt, pwd)

    mail_subject = '=== 일일 테크 뉴스 요약 ===' # 메일 제목 정의

    # MIMEText 객체 생성
    msg = MIMEText(html_body, 'html', 'utf-8')
    msg['Subject'] = Header(mail_subject, 'utf-8')
    msg['From'] = mail_accnt
    msg['To'] = ', '.join(to_mail_list)

    # 메일 전송
    mail_server.sendmail(mail_accnt, to_mail_list, msg.as_string())
    logger.info(f"메일 발송 성공: {mail_subject}")

    mail_server.quit()

# JSON 파일 로드
def load_news_from_json(file_path):
    """
    JSON 파일에서 뉴스 데이터를 로드합니다.

    Args:
        file_path (str): JSON 파일 경로.

    Returns:
        list: 뉴스 기사 딕셔너리 리스트.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            news_data = json.load(f)
        return news_data
    except FileNotFoundError:
        logger.error(f"오류: 파일을 찾을 수 없습니다 - {file_path}")
        raise
    except json.JSONDecodeError:
        logger.error(f"오류: JSON 디코딩 오류 - {file_path}")
        raise
    except Exception as e:
        msg = traceback.format_exc()
        logger.error(msg)
        raise

def main(pwd: str):
    # 사용자 정보 설정 (실제 정보로 변경 필요)
    SENDER_EMAIL = ""  # 발신자 이메일 주소 (실제 네이버 이메일로 변경)
    SENDER_PASSWORD = pwd + 'CH'   # 사용자 암호 (실제 네이버 이메일 비밀번호로 변경)
    RECEIVER_EMAIL_LIST = []  # 수신자 이메일 주소 (실제 수신자 이메일로 변경)
    JSON_FILE_PATH = f"{pjt_home_path}/data/summarized_news.json"  # JSON 파일 경로
    
    RECEIVER_EMAIL_LIST = load_recv_emails_from_config()
    if not RECEIVER_EMAIL_LIST:
        logger.error("RECEIVER_EMAIL_LIST is empty!!") 
        sys.exit(1)
        
    SENDER_EMAIL = RECEIVER_EMAIL_LIST[0]
    
    if pwd == "":
        logger.error("pwd is empty!!")
        sys.exit(1)

    try:
        # 뉴스 데이터 로드
        news_articles = load_news_from_json(JSON_FILE_PATH)

        if news_articles:
            # 이메일 발송
            send_email_with_news(SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL_LIST, news_articles)
            # send_mail(SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL_LIST, 'test', 'text')
        else:
            logger.warning("발송할 뉴스 데이터가 없습니다.")
    except Exception as e:
        msg = traceback.format_exc()
        logger.error(msg)
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("pwd",
                        type=str,
                        default="",
                        nargs='?')

    args = parser.parse_args()
    main(args.pwd)