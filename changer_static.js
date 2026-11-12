//changer_static.js
// 2022-2024 Marc Schubert <schubert.mc.ai@gmail.com>

console.log("Starting static changer");
var word_data;

function initializeWithData(data, css) {
    word_data = data;
    let wordMap = new Map(word_data.map(item => [item.word.toLowerCase(), item]));
    installStyle(editableElement.shadowRoot, css || '');
    colorizeWords(editableElement.shadowRoot.querySelector('anki-editable'), wordMap);
    placeCaretAtEnd(editableElement.shadowRoot.querySelector('anki-editable'));
}


// Collect the <anki-editable> of every field (each lives in its own shadow root).
function getAllEditables() {
    let editables = [];
    document.querySelectorAll('*').forEach(el => {
        if (el.shadowRoot) {
            let ed = el.shadowRoot.querySelector('anki-editable');
            if (ed) editables.push(ed);
        }
    });
    return editables;
}

// The spans carry only their group (class + data-group); the colours come from a stylesheet. Reviewing uses the
// one in the collection's media folder (imported by the note type), but the editor does not apply the note
// type's CSS to field contents, so the same rules are injected into each field's shadow root here.
function installStyle(root, css) {
    let style = root.querySelector('style#colorcoding-groups');
    if (!style) {
        style = document.createElement('style');
        style.id = 'colorcoding-groups';
        root.appendChild(style);
    }
    if (style.textContent !== css) {
        style.textContent = css;
    }
}

// Colorize every field at once, without touching the caret. Used on note load.
// Fields render asynchronously, so retry briefly until they appear.
function initializeAllFields(data, css) {
    word_data = data;
    let wordMap = new Map(word_data.map(item => [item.word.toLowerCase(), item]));

    let attempts = 0;
    function run() {
        let editables = getAllEditables();
        if (editables.length === 0 && attempts < 10) {
            attempts++;
            setTimeout(run, 100);
            return;
        }
        editables.forEach(ed => {
            installStyle(ed.getRootNode(), css || '');
            colorizeWords(ed, wordMap);
        });
    }
    run();
}



var editableElement = document.activeElement;


function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Function to retrieve all text nodes within an element
function getAllTextNodes(element) {
    let textNodes = [];
    let walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
    let node;

    while ((node = walker.nextNode())) {
        textNodes.push(node);
    }
    return textNodes;
}

// Function to retrieve all nodes within an element
function getAllNodes(element) {
    let allNodes = [];
    let walker = document.createTreeWalker(element, NodeFilter.SHOW_ALL, null, false);
    let node;

    while ((node = walker.nextNode())) {
        allNodes.push(node);
    }
    return allNodes;
}


function colorizeWords(editableElement, wordMap) {

    console.log("Starting colorize words");
    removeAllColorCodingSpans(editableElement);
    // Merge adjacent text nodes left behind by span removal, so multi-word
    // phrases aren't split across several text nodes and stay matchable.
    editableElement.normalize();

    // Build one regex from every configured term. Sorting longest-first means a
    // phrase like "south-east Europe" wins over its component words ("east",
    // "Europe").
    //
    // Boundaries are decided per term rather than wrapping everything in \b,
    // because \b only marks a boundary next to a word character. Terms common in
    // code ("C++", "$var", "->", "std::vector", ".map") begin or end with
    // punctuation, so a blanket \b would never let them match. We therefore add a
    // word-boundary guard only on a side that actually ends in a word character;
    // a side ending in punctuation gets no guard (punctuation is self-delimiting).
    const rawTerms = Array.from(wordMap.keys())
        .filter(term => term.length > 0)
        .sort((a, b) => b.length - a.length);

    if (rawTerms.length === 0) {
        return;
    }

    const isWordChar = ch => /\w/.test(ch);
    const alternatives = rawTerms.map(term => {
        const lead = isWordChar(term[0]) ? '(?<!\\w)' : '';
        const trail = isWordChar(term[term.length - 1]) ? '(?!\\w)' : '';
        return lead + escapeRegExp(term) + trail;
    });

    const pattern = new RegExp('(' + alternatives.join('|') + ')', 'gi');

    const textNodes = getAllTextNodes(editableElement);
    textNodes.forEach(node => {
        const text = node.textContent;
        if (!text) {
            return;
        }

        let fragment = document.createDocumentFragment();
        let lastIndex = 0;
        let matched = false;
        let match;

        pattern.lastIndex = 0;
        while ((match = pattern.exec(text)) !== null) {
            matched = true;
            const matchedText = match[0];
            const start = match.index;

            // text before this match stays as plain text
            if (start > lastIndex) {
                fragment.appendChild(document.createTextNode(text.slice(lastIndex, start)));
            }

            const matched_item = wordMap.get(matchedText.toLowerCase());
            let span = document.createElement('span');
            span.textContent = matchedText;
            span.className = 'ColorCoding';
            span.dataset.name = matchedText;
            span.dataset.group = matched_item.group;
            fragment.appendChild(span);

            lastIndex = start + matchedText.length;
            // guard against any zero-length match to avoid an infinite loop
            if (pattern.lastIndex === start) {
                pattern.lastIndex++;
            }
        }

        if (!matched) {
            return;
        }

        // trailing text after the last match
        if (lastIndex < text.length) {
            fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
        }

        node.parentNode.replaceChild(fragment, node);
    });
}


function placeCaretAtEnd(el) {
    el.focus();
    if (typeof window.getSelection != "undefined"
        && typeof document.createRange != "undefined") {
        var range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    } else if (typeof document.body.createTextRange != "undefined") {
        var textRange = document.body.createTextRange();
        textRange.moveToElementText(el);
        textRange.collapse(false);
        textRange.select();
    }
}



function removeAllColorCodingSpans(editableElement) {
    const spans = editableElement.querySelectorAll('.ColorCoding');
    spans.forEach(span => {
        const parent = span.parentNode;
        // Move all child nodes of the span back to the parent node just before the span
        while (span.firstChild) {
            parent.insertBefore(span.firstChild, span);
        }
        // Remove the span from the DOM
        parent.removeChild(span);
    });
}
