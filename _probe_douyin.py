"""探测抖音创作者中心的热门话题DOM结构"""
import json
import websocket

ws_url = "ws://localhost:9222/devtools/page/6B7E18CAFFFEA35F9FD6F172DED2361B"
ws = websocket.create_connection(ws_url, timeout=15)

# 执行JS探测热门话题区域
js = """
(function() {
    var text = document.body.innerText;
    
    // 1. 找到所有包含"热度"的文字位置，看它周围的结构
    var allEls = document.querySelectorAll('*');
    var heatElements = [];
    for (var i = 0; i < allEls.length && heatElements.length < 10; i++) {
        var el = allEls[i];
        var t = el.innerText || '';
        if (t.match(/^\\s*[\\d.]+万?\\s*$/) && el.children.length === 0 && t.length < 10) {
            // 这可能是一个热度数值元素
            var parent = el.parentElement;
            var grandParent = parent ? parent.parentElement : null;
            // 往上找标题
            var titleEl = parent ? parent.previousElementSibling : null;
            var title = '';
            if (titleEl) title = titleEl.innerText.trim().slice(0, 50);
            if (!title && grandParent) {
                titleEl = grandParent.previousElementSibling;
                if (titleEl) title = titleEl.innerText.trim().slice(0, 50);
            }
            
            heatElements.push({
                heatValue: t.trim(),
                heatClass: el.className ? el.className.slice(0, 60) : '',
                parentClass: parent ? (parent.className || '').slice(0, 60) : '',
                possibleTitle: title,
                parentTag: parent ? parent.tagName : ''
            });
        }
    }
    
    // 2. 找"热门话题"区域
    var hotTopicSection = null;
    for (var el of allEls) {
        if (el.innerText && el.innerText.includes('热门话题') && el.children.length < 5) {
            hotTopicSection = {
                tag: el.tagName,
                class: (el.className || '').slice(0, 80),
                text: el.innerText.slice(0, 100)
            };
            break;
        }
    }
    
    // 3. 找"猜你喜欢"区域
    var guessSection = null;
    for (var el of allEls) {
        if (el.innerText && el.innerText.includes('猜你喜欢') && el.children.length < 5) {
            guessSection = {
                tag: el.tagName,
                class: (el.className || '').slice(0, 80)
            };
            break;
        }
    }
    
    return JSON.stringify({
        heatElements: heatElements,
        hotTopicSection: hotTopicSection,
        guessSection: guessSection,
        pageTextSnippet: text.slice(0, 500)
    });
})()
"""

ws.send(json.dumps({
    "id": 1,
    "method": "Runtime.evaluate",
    "params": {"expression": js, "returnByValue": True}
}))

result = json.loads(ws.recv())
ws.close()

data = json.loads(result["result"]["result"]["value"])
print("=== 页面文字(前500字) ===")
print(data["pageTextSnippet"][:300])
print("\n=== 热度数值元素 ===")
for h in data["heatElements"][:5]:
    print(f"  热度={h['heatValue']} class={h['heatClass']}")
    print(f"    parentClass={h['parentClass']} 可能标题={h['possibleTitle']}")
print("\n=== 热门话题区域 ===")
print(json.dumps(data["hotTopicSection"], ensure_ascii=False) if data["hotTopicSection"] else "未找到")
print("\n=== 猜你喜欢区域 ===")
print(json.dumps(data["guessSection"], ensure_ascii=False) if data["guessSection"] else "未找到")
