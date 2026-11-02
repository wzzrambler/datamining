"""Exercise actual canvas clicks and the offline detail panel in Edge."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / '.test_tmp/p6_details'


def main():
    QA.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1500, 'height': 1050})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        for scene in ['广州长隆', '九寨沟', '珠海长隆']:
            time_theme_order_checks = 0
            page.goto((ROOT / f'outputs/P6/可视化可交互知识图谱/{scene}知识网络_交互版.html').as_uri())
            page.wait_for_function('view.nodes.length > 0')
            for kind in ['POI', 'ROUTE', 'TRANSPORT_LEG', 'TRANSPORT']:
                # Pick a rich record, then click its exact canvas coordinates.
                node = page.evaluate('''kind => view.nodes.filter(n=>n.label===kind).sort((a,b)=>
                    (view.details[b.id].records.length-view.details[a.id].records.length))[0]''', kind)
                point = page.evaluate('id=>point(index.get(id))', node['id'])
                bounds = page.locator('#canvas').bounding_box()
                page.mouse.click(bounds['x'] + point['x'], bounds['y'] + point['y'])
                assert page.evaluate('selected') == node['id']
                assert page.locator('#info table').count() > 0
                owner = page.evaluate('''id=>{const n=view.details[id].records.find(x=>x.id===id);return n?.properties?.advice_themes||[]}''', node['id'])
                if owner:
                    panel = page.locator('#info').text_content()
                    assert '按主题整理的条件与建议' in panel
                    assert all(theme['label'] in panel and theme['summary'] in panel for theme in owner)
                    if '时间汇总 ·' in panel:
                        assert panel.index('时间汇总 ·') < panel.index('按主题整理的条件与建议')
                        time_theme_order_checks += 1
                if kind == 'ROUTE':
                    assert '有序线路步骤' in page.locator('#info').inner_text()
                if kind == 'TRANSPORT_LEG':
                    assert '起点' in page.locator('#info').inner_text()
                    assert '终点' in page.locator('#info').inner_text()
                expected = page.evaluate('''id=>{
                    const d=view.details[id];
                    return ['HAS_FACT','HAS_TIME_SUMMARY','APPLIES_TO','HAS_ADVICE_SUMMARY'].filter(kind=>d.edges.some(e=>
                        e.type===kind&&(kind==='APPLIES_TO'?e.target===id:e.source===id)));
                }''', node['id'])
                for relation, title in [('HAS_FACT','属性记录'),('HAS_TIME_SUMMARY','时间汇总'),('APPLIES_TO','条件、建议与限制'),('HAS_ADVICE_SUMMARY','条件与推荐汇总')]:
                    if relation in expected:
                        assert title in page.locator('#info').inner_text()
                page.screenshot(path=str(QA / f'{scene}-{kind}.png'))
                # Expand all attached categories to catch content/JS failures.
                page.locator('#info details').evaluate_all('(xs)=>xs.forEach(x=>x.open=true)')
                assert page.locator('#info pre').count() > 0
            page.locator('#search').fill('POI-')
            assert '搜索命中' in page.locator('#status').inner_text()
            before = page.evaluate('zoom')
            page.mouse.move(bounds['x']+50, bounds['y']+50)
            page.mouse.wheel(0, -150)
            page.wait_for_timeout(100)
            assert page.evaluate('zoom') > before
            boxes=page.locator('#filters input')
            assert boxes.count()==4
            boxes.first.uncheck()
            assert not page.evaluate("enabled.has('POI')")
            assert time_theme_order_checks > 0
            results.append({'scene':scene,'canvas_click_types':4,'theme_summaries_checked':True,'time_before_themes_order_checks':time_theme_order_checks,'search_zoom_filters':True,'js_errors':list(errors)})
            assert not errors
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(QA/'mobile.png'),full_page=True)
        browser.close()
    (QA/'browser_checks.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(results,ensure_ascii=False))


if __name__ == '__main__':
    main()
