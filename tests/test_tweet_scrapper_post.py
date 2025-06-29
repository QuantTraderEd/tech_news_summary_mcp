import os
import sys
import site
import json
import pytest
import datetime as dt

from unittest.mock import MagicMock, patch, mock_open
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Add project root to the Python path to allow importing from 'app'
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from app.services.tweet_scrapper_post import TweetScraper


@pytest.fixture
def scraper():
    """Pytest fixture to create a TweetScraper instance with mocked dependencies."""
    with patch('app.services.tweet_scrapper_post.ChromeDriverManager'), \
         patch('app.services.tweet_scrapper_post.webdriver.Chrome'):
        
        scraper_instance = TweetScraper()
        # Assign mock driver and wait objects directly to the instance
        scraper_instance.driver = MagicMock()
        scraper_instance.wait = MagicMock()
        yield scraper_instance

def test_set_target_date_range(scraper):
    """Test if the date range is set correctly."""
    start_date = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)
    end_date = dt.datetime(2023, 1, 31, tzinfo=dt.timezone.utc)
    scraper.set_target_date_range(start_date, end_date)
    assert scraper.start_date == start_date
    assert scraper.end_date == end_date

def test_parse_tweet_datetime():
    """Test parsing of tweet datetime strings."""
    # Test valid datetime string
    valid_str = "2025-06-03T22:14:35.000Z"
    expected_dt = dt.datetime(2025, 6, 3, 22, 14, 35, tzinfo=dt.timezone.utc)
    assert TweetScraper().parse_tweet_datetime(valid_str) == expected_dt

    # Test invalid datetime string
    invalid_str = "not-a-valid-date"
    # The function is designed to return dt.datetime.min on failure
    assert TweetScraper().parse_tweet_datetime(invalid_str) == dt.datetime.min

@patch('app.services.tweet_scrapper_post.time.sleep')
def test_login_to_twitter_success(mock_sleep, scraper):
    """Test successful login scenario."""
    mock_user_input = MagicMock()
    mock_next_button = MagicMock()
    mock_password_input = MagicMock()
    mock_password_input.get_attribute.return_value = 'password'
    mock_login_button = MagicMock()
    
    # Configure the wait.until to return the mock elements in sequence
    scraper.wait.until.side_effect = [
        mock_user_input,
        mock_next_button,
        mock_password_input,  # For the dynamic step check
        mock_login_button,    # For the login button
        True                  # For the final login confirmation
    ]

    result = scraper.login_to_twitter("testuser", "testpass", "verification_info")
    
    assert result is True
    scraper.driver.get.assert_called_once_with("https://x.com/login")
    mock_user_input.send_keys.assert_called_once_with("testuser")
    mock_next_button.click.assert_called_once()
    mock_password_input.send_keys.assert_called_once_with("testpass")
    mock_login_button.click.assert_called_once()

@patch('app.services.tweet_scrapper_post.time.sleep')
def test_login_to_twitter_failure_on_timeout(mock_sleep, scraper):
    """Test failed login due to a TimeoutException."""
    scraper.wait.until.side_effect = TimeoutException("Element not found.")

    result = scraper.login_to_twitter("testuser", "testpass", "info")
    assert result is False

@patch('app.services.tweet_scrapper_post.time.sleep')
@patch('app.services.tweet_scrapper_post.open', new_callable=mock_open)
@patch('app.services.tweet_scrapper_post.json.dump')
def test_scrape_user_post_with_date_filtering(mock_json_dump, mock_file_open, mock_sleep, scraper):
    """Test successful scraping and that posts are filtered by date."""
    target_username = "testuser"
    
    # --- Mocking Selenium WebElements ---
    # Mock post 1 (in date range)
    mock_time1 = MagicMock()
    mock_time1.get_attribute.return_value = "2025-06-28T12:00:00.000Z"
    mock_link1 = MagicMock()
    mock_link1.get_attribute.return_value = f"https://x.com/{target_username}/status/1"
    mock_link1.find_element.return_value = mock_time1
    mock_text1 = MagicMock()
    mock_text1.text = "This is a recent tweet."
    
    mock_article1 = MagicMock()
    def find_element_side_effect_1(*args, **kwargs):
        if ".//a[time]" in args[1]: return mock_link1
        if ".//div[@data-testid='tweetText']" in args[1]: return mock_text1
        raise NoSuchElementException
    mock_article1.find_element.side_effect = find_element_side_effect_1

    # Mock post 2 (out of date range)
    mock_time2 = MagicMock()
    mock_time2.get_attribute.return_value = "2024-01-01T12:00:00.000Z"
    mock_link2 = MagicMock()
    mock_link2.get_attribute.return_value = f"https://x.com/{target_username}/status/2"
    mock_link2.find_element.return_value = mock_time2
    mock_text2 = MagicMock()
    mock_text2.text = "This is an old tweet."
    
    mock_article2 = MagicMock()
    def find_element_side_effect_2(*args, **kwargs):
        if ".//a[time]" in args[1]: return mock_link2
        if ".//div[@data-testid='tweetText']" in args[1]: return mock_text2
        raise NoSuchElementException
    mock_article2.find_element.side_effect = find_element_side_effect_2

    scraper.driver.find_elements.return_value = [mock_article1, mock_article2]
    
    # Set a date range that includes post 1 but not post 2
    start_date = dt.datetime(2025, 6, 27, tzinfo=dt.timezone.utc)
    end_date = dt.datetime(2025, 6, 29, tzinfo=dt.timezone.utc)
    scraper.set_target_date_range(start_date, end_date)
    
    scraper.scrape_user_post(target_username)

    # Assert that the profile page was loaded
    scraper.driver.get.assert_called_with(f"https://x.com/{target_username}")
    
    # Assert file was opened for writing
    expected_filepath = f"{pjt_home_path}/data/{target_username}_posts.json"
    mock_file_open.assert_called_once_with(expected_filepath, 'w', encoding='utf-8')

    # Assert json.dump was called with only the correctly filtered post data
    expected_post_data = {
        'data': [{
            'url': f"https://x.com/{target_username}/status/1",
            'id': '1',
            'created_at': "2025-06-28T12:00:00.000Z",
            'text': "This is a recent tweet."
        }]
    }
    mock_json_dump.assert_called_once_with(expected_post_data, mock_file_open(), ensure_ascii=False, indent=4)

@patch('app.services.tweet_scrapper_post.time.sleep')
@patch('app.services.tweet_scrapper_post.open', new_callable=mock_open)
@patch('app.services.tweet_scrapper_post.json.dump')
def test_scrape_user_post_no_posts_found(mock_json_dump, mock_file_open, mock_sleep, scraper):
    """Test scraping when no posts are found on the page."""
    target_username = "nopostuser"
    
    # Simulate find_elements returning an empty list
    scraper.driver.find_elements.return_value = []
    
    scraper.scrape_user_post(target_username)
    
    # Assert that no file was opened and no data was dumped because no posts were collected
    mock_file_open.assert_not_called()
    mock_json_dump.assert_not_called()
