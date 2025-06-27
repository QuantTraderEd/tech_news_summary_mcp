import os
import sys
import site
import logging
import traceback
import json
import smtplib

from email import encoders
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from app.services import send_mail

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

def create_email_body(posts_data):
    """
    게시물 데이터를 기반으로 모바일 가독성이 높은 HTML 이메일 본문을 생성합니다.
    - 요약(summary)이 있으면 번역문은 표시하지 않습니다.
    - 요약이 없고 번역문만 있으면, 레이블과 내용 사이에 줄바꿈을 추가하여 표시합니다.
    """
    if not posts_data:
        return "<p>표시할 게시물 데이터가 없습니다.</p>"

    # --- 이메일 상단 및 스타일 부분 ---
    html_body = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>최신 반도체/테크 Tweet 요약</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 0;
                background-color: #f4f4f4;
                color: #333;
                -webkit-text-size-adjust: 100%;
                -ms-text-size-adjust: 100%;
                width: 100% !important;
            }
            .container {
                width: 100%;
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
                padding: 20px;
                box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
                box-sizing: border-box;
            }
            .post {
                margin-bottom: 25px;
                padding-bottom: 20px;
                border-bottom: 1px solid #eee;
            }
            .post:last-child {
                border-bottom: none;
                margin-bottom: 0;
            }
            .post-header {
                font-size: 14px;
                color: #777;
                margin-bottom: 5px;
            }
            /* --- 추가된 코드: 제목 스타일 --- */
            .post-title {
                font-size: 18px;
                font-weight: bold;
                color: #333333;
                margin-top: 15px;
                margin-bottom: 5px;
            }
            .post-text {
                font-size: 16px;
                line-height: 1.5;
                margin-bottom: 10px;
            }
            .post-summary {
                font-size: 14px;
                color: #555;
                line-height: 1.4;
                background-color: #f9f9f9;
                padding: 10px;
                border-left: 3px solid #007bff;
                margin-top: 10px;
            }
            .post-link {
                color: #007bff;
                text-decoration: none;
                font-size: 14px;
            }
            .post-link:hover {
                text-decoration: underline;
            }
            h2 {
                color: #0056b3;
                text-align: center;
                margin-bottom: 30px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>최신 반도체/테크 Tweet 요약</h2>
    """

    # --- 각 게시물 데이터를 처리하는 루프 (수정된 로직 적용) ---
    for i, post in enumerate(posts_data):
        url = post.get('url', '#')
        created_at = post.get('created_at', '날짜 정보 없음')
        original_text = post.get('text', '원문 없음')
        
        if len(original_text) < 15:
            continue
        
        # --- 수정된 코드: 제목, 번역문, 요약 정보 가져오기 ---
        title = post.get('title')
        translated_text = post.get('translated_text')
        summary = post.get('summary')

        main_content_html = ""

        # 1. 요약(summary)이 있는지 확인하고, 있으면 제목과 요약 HTML을 생성합니다.
        if summary:
            title_html = ""
            # 제목이 있으면 h3 태그로 만듭니다.
            if title:
                title_html = f'<h3 class="post-title">{title}</h3>'
            
            summary_html = ""
            if isinstance(summary, str):
                if summary.strip().startswith('*'):
                    summary_items = summary.strip().split('\n*')
                    summary_html += "<ul>"
                    for item in summary_items:
                        clean_item = item.strip().lstrip('*').strip()
                        if clean_item:
                            summary_html += f"<li>{clean_item}</li>"
                    summary_html += "</ul>"
                else:
                    summary_html = summary.replace('\n', '<br>')
            else:
                summary_html = "요약 없음"
            
            # --- 수정된 코드: 제목 HTML을 요약 블록 앞에 추가 ---
            main_content_html = f"""
            {title_html}
            <div class="post-summary">
                <strong>요약:</strong><br>{summary_html}
            </div>
            """

        # 2. 요약이 없고 번역문(translated_text)만 있는 경우, 번역문 HTML을 생성합니다.
        elif translated_text:
            processed_text = translated_text.replace('\n', '<br>')
            main_content_html = f"""
            <div class="post-text">
                <strong>번역문:</strong><br>
                {processed_text}                
            </div>
            """

        # --- 게시물 HTML 구조를 만듭니다 ---
        html_body += f"""
            <div class="post">
                <div class="post-header">게시일시: {created_at}</div>
                <div class="post-text"><strong>원문:</strong> {original_text}</div>
                {main_content_html}
                <p><a href="{url}" class="post-link">원본 게시물 보기</a></p>
            </div>
        """
        
        if i < len(posts_data) - 1:
            html_body += "<br><br>"

    # --- 이메일 하단 부분 ---
    html_body += """
        </div>
    </body>
    </html>
    """
    return html_body

def send_email_with_tweet(sender_email, sender_password, receiver_email_list, mail_subject, mail_body):
    """
    이메일을 발송합니다.    
    """
    
    mail_accnt = sender_email
    pwd = sender_password
    to_mail_list = receiver_email_list
    logger.info(f"발신자: {mail_accnt}, 수신자: {to_mail_list}")
    

    mail_server = send_mail.mail_server_login(mail_accnt, pwd)

    # MIMEText 객체 생성
    # msg = MIMEText(html_body, 'html', 'utf-8')
    msg = MIMEMultipart("alternative")
    msg['Subject'] = Header(mail_subject, 'utf-8')
    msg['From'] = mail_accnt
    msg['To'] = ', '.join(to_mail_list)
    
    # HTML 본문 추가
    msg.attach(MIMEText(mail_body, "html", "utf-8"))    

    # 메일 전송
    mail_server.sendmail(mail_accnt, to_mail_list, msg.as_string())
    logger.info(f"메일 발송 성공: {mail_subject}")

    mail_server.quit()
    
def main(pwd: str):
    # 사용자 정보 설정 (실제 정보로 변경 필요)
    SENDER_EMAIL = "ggtt7@naver.com"  # 발신자 이메일 주소 (실제 네이버 이메일로 변경)
    SENDER_PASSWORD = pwd + 'CH'   # 사용자 암호 (실제 네이버 이메일 비밀번호로 변경)
    RECEIVER_EMAIL_LIST = ["ggtt7@naver.com"]  # 수신자 이메일 주소 (실제 수신자 이메일로 변경)
    JSON_FILE_PATH = f"{pjt_home_path}/data/summarized_posts.json"  # JSON 파일 경로
    
    if pwd == "":
        logger.error("pwd is empty!!")
        sys.exit(1)

    try:
        # Tweet 데이터 로드
        summarized_posts = send_mail.load_news_from_json(JSON_FILE_PATH)
        # 요약 결과를 'created_at' 기준으로 정렬
        summarized_posts = sorted(summarized_posts, key=lambda x: (x['created_at']), reverse=True)

        if summarized_posts:
            # 이메일 본문 생성
            email_body_html = create_email_body(summarized_posts)
            # 이메일 발송
            mail_subject = '=== 일일 테크 Tweet 요약 ===' # 메일 제목 정의
            send_email_with_tweet(SENDER_EMAIL, SENDER_PASSWORD, RECEIVER_EMAIL_LIST, mail_subject, email_body_html)
            
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