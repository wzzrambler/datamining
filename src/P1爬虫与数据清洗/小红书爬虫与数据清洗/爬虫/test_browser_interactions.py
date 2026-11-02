"""显式运行的本地 Edge 控件测试；新临时上下文，阻断所有外部页面请求。"""
import random
import unittest
from playwright.sync_api import sync_playwright
from src.interactions import browse_images, expand_comments, wait_comments_ready


class TwoReplies:
    def random(self): return .9
    def uniform(self, a, b): return 3
    def sample(self, population, count): return population[:count]


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.requests = []
        def abort(route):
            self.requests.append(route.request.url)
            route.abort()
        self.context.route('**/*', abort)
        self.page = self.context.new_page()
        self.pauses, self.saves = [], []

    def tearDown(self):
        self.context.close()

    def test_visits_all_images_without_wrapping(self):
        uri = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        slides = ''.join(f'<div class="swiper-slide {"swiper-slide-active" if i==0 else ""}" data-index="{i}"><img src="{uri}"></div>' for i in range(3))
        self.page.set_content('''<style>.swiper-slide{display:none}.swiper-slide-active{display:block}.media-container{width:300px;height:200px}</style>
          <div id="noteContainer"><div class="media-container"><div class="note-slider">''' + slides + '''</div>
          <button class="arrow-controller right" onclick="next()">右</button></div></div>
          <script>window.flips=0;let i=0;function next(){flips++;i=(i+1)%3;document.querySelectorAll('.swiper-slide').forEach((x,n)=>x.classList.toggle('swiper-slide-active',n===i));}</script>''')
        result = {}
        browse_images(self.page, 3, lambda: None, self.pauses.append, self.saves.append,
                      lambda *args: None, result, random.Random(1))
        self.assertEqual(result['visited'], [1, 2, 3])
        self.assertEqual(self.page.evaluate('window.flips'), 2)
        self.assertTrue(all(1 <= wait <= 5 for wait in self.pauses))
        self.assertEqual(result['status'], 'all_images_viewed')
        self.assertEqual(self.requests, [])

    def set_comments(self, count):
        self.page.set_content('<div class="comments-container">' + ''.join(
            '<div class="parent-comment"><div class="comment-item">主评论</div><div class="replies"></div>'
            '<button class="show-more" onclick="this.parentElement.querySelector(\'.replies\').innerHTML=\'<div class=&quot;comment-item&quot;>回复</div>\';this.remove()">展开 1 条回复</button>'
            '<button class="reply" onclick="window.wrongClicks++">回复</button></div>' for _ in range(count))
            + '</div><script>window.wrongClicks=0</script>')

    def test_randomly_expands_two_distinct_reply_threads(self):
        self.set_comments(3)
        result = {}
        expand_comments(self.page, lambda: None, self.pauses.append, self.saves.append,
                        lambda *args: None, result, TwoReplies())
        self.assertEqual(result['requested'], 2)
        self.assertEqual(len(result['expanded']), 2)
        self.assertEqual(self.page.locator('.replies .comment-item').count(), 2)
        self.assertEqual(self.page.evaluate('window.wrongClicks'), 0)
        self.assertEqual(self.requests, [])

    def test_insufficient_threads_means_no_expansion(self):
        self.set_comments(1)
        result = {}
        expand_comments(self.page, lambda: None, self.pauses.append, self.saves.append,
                        lambda *args: None, result, TwoReplies())
        self.assertEqual(result['requested'], 2)
        self.assertEqual(result['actual'], 0)
        self.assertEqual(self.page.locator('.replies .comment-item').count(), 0)
        self.assertEqual(self.requests, [])

    def test_single_reply_validation_expands_only_one_thread(self):
        self.set_comments(3)
        result = {}
        expand_comments(self.page, lambda: None, self.pauses.append, self.saves.append,
                        lambda *args: None, result, TwoReplies(), test_one=True)
        self.assertEqual(result['requested'], 1)
        self.assertEqual(len(result['expanded']), 1)
        self.assertEqual(self.page.locator('.replies .comment-item').count(), 1)
        self.assertEqual(self.page.evaluate('window.wrongClicks'), 0)
        self.assertEqual(self.requests, [])

    def test_comment_waits_for_delayed_dom_and_reply_control(self):
        self.page.set_content('''<div class="comments-container"><div class="loading">加载中</div></div>
          <script>
          setTimeout(() => document.querySelector('.comments-container').innerHTML =
            '<div class="parent-comment"><div class="comment-item">主评论</div></div>', 250);
          setTimeout(() => document.querySelector('.parent-comment').insertAdjacentHTML('beforeend',
            '<button class="show-more">展开 1 条回复</button>'), 650);
          </script>''')
        result = wait_comments_ready(self.page, lambda: None, lambda *args: None,
                                     lambda: {'count': 1}, seconds=4)
        self.assertEqual(result['loaded_root_comments'], 1)
        self.assertEqual(self.page.locator('.show-more').count(), 1)
        self.assertEqual(self.requests, [])

    def test_empty_comment_response_is_confirmed_not_timed_out(self):
        self.page.set_content('<div class="comments-container">暂无评论</div>')
        result = wait_comments_ready(self.page, lambda: None, lambda *args: None,
                                     lambda: {'count': 0}, seconds=2, stable_seconds=.1)
        self.assertEqual(result['loaded_root_comments'], 0)

    def test_visible_comment_shell_without_response_is_not_ready(self):
        self.page.set_content('<div class="comments-container">加载中</div>')
        with self.assertRaisesRegex(RuntimeError, '不能将超时视为没有可展开回复'):
            wait_comments_ready(self.page, lambda: None, lambda *args: None,
                                lambda: None, seconds=.4, stable_seconds=.1)

    def test_loading_indicator_prevents_false_empty_success(self):
        self.page.set_content('<div class="comments-container"><div class="loading">加载中</div></div>')
        with self.assertRaises(RuntimeError):
            wait_comments_ready(self.page, lambda: None, lambda *args: None,
                                lambda: {'count': 0}, seconds=.4, stable_seconds=.1)


if __name__ == '__main__':
    unittest.main()
