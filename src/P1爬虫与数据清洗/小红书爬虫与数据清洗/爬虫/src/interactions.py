"""普通页面控件交互；不构造接口请求，不强制点击隐藏控件。"""
import random
import re
import time


REPLY_CONTROL = '.show-more, .more-replies, .show-more-replies, [data-testid="expand-replies"], button, [role="button"]'
REPLY_TEXT = re.compile(r'^(?:展开(?:更多|全部)?\s*(?:\d+\s*条)?\s*回复|查看(?:更多|全部)?\s*(?:\d+\s*条)?\s*回复)$')


def comment_draw(available, rng=random):
    p = rng.random()
    requested = 0 if p < .5 else 1 if p < .75 else 2
    return {'requested': requested, 'actual': requested if available >= requested else 0,
            'available': available, 'reason': 'insufficient_candidates' if available < requested else 'sampled'}


def poll(page, check, condition, label, seconds=12):
    deadline = time.monotonic() + seconds
    while True:
        check()
        if condition():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(label)
        page.wait_for_timeout(200)


def wait_comments_ready(page, check, log, load_state=None, seconds=20, stable_seconds=1):
    """等到当前笔记首批评论响应完成且 DOM 稳定；不滚动、不补发接口。"""
    deadline = time.monotonic() + seconds
    previous, stable_since = None, None
    log('等待首批评论加载完成')
    while True:
        check()
        container = page.locator('.comments-container:visible').first
        state = load_state() if load_state is not None else None
        if container.count():
            parents = container.locator('.parent-comment')
            count = parents.count()
            loading = container.locator('[aria-busy="true"]:visible, .loading:visible, .loading-container:visible, .loading-spinner:visible').count()
            # 首批响应给出空列表时，零条评论也是明确完成状态。
            expected = state.get('count') if state is not None else None
            response_ready = (state is not None) if load_state is not None else count > 0
            rendered = count >= expected if expected is not None else count > 0
            signature = (count, container.inner_text())
            if response_ready and rendered and not loading:
                if signature != previous:
                    previous, stable_since = signature, time.monotonic()
                elif stable_since is not None and time.monotonic() - stable_since >= stable_seconds:
                    result = {'status': 'ready', 'loaded_root_comments': count,
                              'response_root_comments': expected, 'scope': 'initial_loaded_batch'}
                    log('首批评论已加载', f'{count} 条主评论；不代表全部评论已采集')
                    return result
            else:
                previous, stable_since = None, None
        if time.monotonic() >= deadline:
            raise RuntimeError('首批评论加载未确认完成，停止；不能将超时视为没有可展开回复。')
        page.wait_for_timeout(200)


def browse_images(page, expected, check, pause, save, log, result, rng=random):
    result.update(expected=expected, visited=[], flips=[], status='started')
    media = page.locator('#noteContainer .media-container:visible').first
    if not media.count():
        media = page.locator('.media-container:visible').first
    if not media.count() or not media.locator('.note-slider:visible').count():
        result['status'] = 'no_image_carousel'
        return
    slider = media.locator('.note-slider:visible').first
    indices = slider.locator('.swiper-slide').evaluate_all('nodes => [...new Set(nodes.map(n => n.getAttribute("data-index") ?? n.getAttribute("data-swiper-slide-index")).filter(x => x !== null))]')
    total = expected or len(indices)
    result['expected'] = total
    if not total:
        raise RuntimeError('无法确认图片总数，停止翻图。')

    def current():
        active = slider.locator('.swiper-slide-active').first
        if not active.count():
            return None
        value = active.get_attribute('data-index') or active.get_attribute('data-swiper-slide-index')
        return int(value) if value is not None and value.isdigit() else None

    def ready():
        images = slider.locator('.swiper-slide-active img')
        return images.count() > 0 and images.evaluate_all('nodes => nodes.every(n => n.complete && n.naturalWidth > 0)')

    def click_arrow(direction, destination):
        check()
        delay = rng.uniform(1, 5)
        log('翻图前等待', f'{delay:.2f} 秒')
        pause(delay)
        media.hover(timeout=5000)
        arrow = media.locator(f'.arrow-controller.{direction}:not(.forbidden):not([aria-disabled="true"]):visible').first
        if not arrow.count():
            raise RuntimeError('未找到可用的图片翻页箭头，停止，避免误点。')
        log('点击图片箭头', f'{direction}，目标第 {destination+1}/{total} 张')
        arrow.click(timeout=5000)
        poll(page, check, lambda: current() == destination, '图片序号未按预期变化，停止，避免循环。')
        result['flips'].append({'direction': direction, 'destination': destination+1, 'delay_seconds': delay})

    check()
    index = current()
    if index is None or not 0 <= index < total:
        raise RuntimeError('当前图片序号不可确认，停止翻图。')
    # 从中途恢复时先使用普通左箭头回到第一张；不会依赖轮播环绕。
    while index > 0:
        click_arrow('left', index-1)
        index -= 1
    for index in range(total):
        poll(page, check, ready, '当前图片未加载成功，停止翻图。')
        result['visited'].append(index+1)
        log('图片加载完成', f'第 {index+1}/{total} 张')
        save(f'image_{index+1:03d}')
        if index+1 < total:
            click_arrow('right', index+1)
    result['status'] = 'all_images_viewed'
    save('images_finished')


def expand_comments(page, check, pause, save, log, result, rng=random, test_one=False):
    check()
    candidates = []
    parents = page.locator('.comments-container .parent-comment')
    for index in range(parents.count()):
        parent = parents.nth(index)
        controls = parent.locator(REPLY_CONTROL).filter(has_text=REPLY_TEXT)
        button = next((controls.nth(i) for i in range(controls.count()) if controls.nth(i).is_visible()), None)
        if button is not None:
            candidates.append((index, parent, button))
    if test_one:
        result.update(requested=1, actual=1 if candidates else 0, available=len(candidates),
                      reason='single_reply_test' if candidates else 'insufficient_candidates')
    else:
        result.update(comment_draw(len(candidates), rng))
    result.update(expanded=[], status='not_expanded')
    log('评论展开抽样', f'抽中 {result["requested"]} 条，可展开 {len(candidates)} 条，执行 {result["actual"]} 条')
    if not result['actual']:
        save('comments_not_expanded')
        return
    for ordinal, (index, parent, button) in enumerate(rng.sample(candidates, result['actual']), 1):
        check()
        delay = rng.uniform(1, 5)
        log('展开回复前等待', f'{delay:.2f} 秒')
        pause(delay)
        before = parent.locator('.comment-item:visible').count()
        log('展开一条评论的回复', f'已加载列表第 {index+1} 条')
        button.click(timeout=5000)
        poll(page, check, lambda: parent.locator('.comment-item:visible').count() > before,
             '展开回复后页面没有可确认的变化，停止。')
        result['expanded'].append({'loaded_comment_position': index+1, 'delay_seconds': delay})
        save(f'comment_expand_{ordinal:02d}')
    result['status'] = 'expanded'
