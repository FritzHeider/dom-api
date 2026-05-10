"""
JavaScript code constants for React fiber tree walking and state extraction.

These are production Python string constants containing complete, ready-to-execute JS code
for extracting conversation state, messages, and response data from ChatGPT's React app.
"""

from __future__ import annotations

# Walk the React fiber tree to find component instances with message state
REACT_FIBER_WALKER_JS = """
(function() {
    // Walk React fiber tree to find component instances with message state
    function getFiberRoot() {
        // Find root fiber from DOM element
        const root = document.getElementById('__next') || document.body;
        const keys = Object.keys(root);
        const fiberKey = keys.find(
            k => k.startsWith('__reactFiber') ||
                 k.startsWith('__reactInternalInstance') ||
                 k.startsWith('__reactProps')
        );
        return fiberKey ? root[fiberKey] : null;
    }

    function walkFiber(fiber, depth, maxDepth, collector) {
        if (!fiber || depth > maxDepth) return;

        try {
            // Check if this fiber has conversation/message state
            const state = fiber.memoizedState;
            const props = fiber.memoizedProps;

            if (props && typeof props === 'object') {
                // Look for conversation data in props
                if (props.conversation) {
                    collector.push({
                        type: 'props',
                        dataType: 'conversation',
                        data: props.conversation
                    });
                }
                if (props.messages) {
                    collector.push({
                        type: 'props',
                        dataType: 'messages',
                        data: props.messages
                    });
                }
                if (props.chatHistory) {
                    collector.push({
                        type: 'props',
                        dataType: 'chatHistory',
                        data: props.chatHistory
                    });
                }
            }

            if (state && typeof state === 'object') {
                let hookState = state;
                let hookIndex = 0;

                while (hookState) {
                    if (hookState.memoizedState && typeof hookState.memoizedState === 'object') {
                        const ms = hookState.memoizedState;

                        if (ms.messages) {
                            collector.push({
                                type: 'state',
                                dataType: 'messages',
                                hookIndex: hookIndex,
                                data: ms.messages
                            });
                        }
                        if (ms.conversation) {
                            collector.push({
                                type: 'state',
                                dataType: 'conversation',
                                hookIndex: hookIndex,
                                data: ms.conversation
                            });
                        }
                        if (ms.chatMessages) {
                            collector.push({
                                type: 'state',
                                dataType: 'chatMessages',
                                hookIndex: hookIndex,
                                data: ms.chatMessages
                            });
                        }
                    }

                    hookState = hookState.next;
                    hookIndex++;
                }
            }
        } catch(e) {
            // Silently skip fibers with access errors
        }

        // Recursively walk children and siblings
        if (fiber.child) walkFiber(fiber.child, depth + 1, maxDepth, collector);
        if (fiber.sibling) walkFiber(fiber.sibling, depth + 1, maxDepth, collector);
    }

    try {
        const root = getFiberRoot();
        const results = [];
        if (root) {
            walkFiber(root, 0, 50, results);
        }
        return results;
    } catch(e) {
        return [];
    }
})()
"""

# Extract messages using multiple strategies
EXTRACT_MESSAGES_JS = """
(function() {
    // Try multiple strategies to extract messages from page state

    // Strategy 1: __NEXT_DATA__ (Next.js page data)
    try {
        const nextData = window.__NEXT_DATA__;
        if (nextData && nextData.props && nextData.props.pageProps) {
            const pageProps = nextData.props.pageProps;
            if (pageProps.serverResponse && pageProps.serverResponse.data) {
                const messages = pageProps.serverResponse.data.messages;
                if (Array.isArray(messages)) {
                    return {
                        source: 'next_data_messages',
                        data: messages
                    };
                }
            }
            if (pageProps.initialState && pageProps.initialState.messages) {
                return {
                    source: 'next_data_initial_state',
                    data: pageProps.initialState.messages
                };
            }
        }
    } catch(e) {}

    // Strategy 2: Redux/Zustand store via window
    try {
        if (window.__REDUX_STORE__) {
            const state = window.__REDUX_STORE__.getState();
            if (state && state.messages) {
                return { source: 'redux', data: state.messages };
            }
            if (state && state.chat) {
                return { source: 'redux_chat', data: state.chat };
            }
        }
    } catch(e) {}

    // Strategy 3: Check for Zustand store
    try {
        if (window.__ZUSTAND__) {
            const state = window.__ZUSTAND__.getState();
            if (state && state.messages) {
                return { source: 'zustand', data: state.messages };
            }
        }
    } catch(e) {}

    // Strategy 4: React context via fiber
    try {
        const main = document.querySelector('main') || document.querySelector('[role="main"]');
        if (main) {
            const keys = Object.keys(main);
            const fiberKey = keys.find(k => k.startsWith('__reactFiber'));
            if (fiberKey) {
                let fiber = main[fiberKey];
                let attempts = 0;

                while (fiber && attempts < 100) {
                    // Check memoized state in hooks
                    if (fiber.memoizedState) {
                        let hookState = fiber.memoizedState;
                        while (hookState) {
                            if (hookState.memoizedState && hookState.memoizedState.messages) {
                                return {
                                    source: 'fiber_hook',
                                    data: hookState.memoizedState.messages
                                };
                            }
                            hookState = hookState.next;
                        }
                    }

                    fiber = fiber.return || fiber.child;
                    attempts++;
                }
            }
        }
    } catch(e) {}

    return null;
})()
"""

# Extract the latest assistant message from the DOM
EXTRACT_LATEST_RESPONSE_JS = """
(function() {
    // Get the latest assistant message from DOM as fallback

    // Strategy 1: data-message-author-role attribute
    const messagesWithRole = document.querySelectorAll('[data-message-author-role="assistant"]');
    if (messagesWithRole.length > 0) {
        const last = messagesWithRole[messagesWithRole.length - 1];

        // Try to get message ID
        const msgId = last.getAttribute('data-message-id') ||
                      last.getAttribute('data-message-uuid') ||
                      last.getAttribute('id') ||
                      null;

        // Get text content, preferring markdown container
        let contentEl = last.querySelector('.markdown');
        if (!contentEl) contentEl = last.querySelector('.prose');
        if (!contentEl) contentEl = last.querySelector('[class*="prose"]');
        if (!contentEl) contentEl = last.querySelector('article');
        if (!contentEl) contentEl = last;

        return {
            message_id: msgId,
            content: (contentEl.innerText || contentEl.textContent || '').trim(),
            html: contentEl.innerHTML,
            is_complete: !document.querySelector('[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label*="Stop"]'),
            source: 'dom_role_attr'
        };
    }

    // Strategy 2: Article tags with role
    const articles = document.querySelectorAll('article[data-message-role="assistant"]');
    if (articles.length > 0) {
        const last = articles[articles.length - 1];
        return {
            message_id: last.getAttribute('data-message-id') || null,
            content: (last.innerText || last.textContent || '').trim(),
            html: last.innerHTML,
            is_complete: !document.querySelector('[data-testid="stop-button"]'),
            source: 'dom_article'
        };
    }

    // Strategy 3: Last group/message container
    const groups = document.querySelectorAll('.group, [class*="message"], [class*="response"]');
    if (groups.length > 0) {
        const last = groups[groups.length - 1];
        let textEl = last.querySelector('.markdown') || last.querySelector('.prose') || last;
        return {
            message_id: null,
            content: (textEl.innerText || textEl.textContent || '').trim(),
            html: textEl.innerHTML,
            is_complete: !document.querySelector('[data-testid="stop-button"]'),
            source: 'dom_last_group'
        };
    }

    return null;
})()
"""

# Extract conversation ID from URL or page data
EXTRACT_CONVERSATION_ID_JS = """
(function() {
    // Try URL first
    const urlMatch = window.location.href.match(/\\/c\\/([a-zA-Z0-9-]+)/);
    if (urlMatch) {
        return {
            id: urlMatch[1],
            source: 'url'
        };
    }

    // Try __NEXT_DATA__
    try {
        const nd = window.__NEXT_DATA__;
        if (nd && nd.query && nd.query.id) {
            return { id: nd.query.id, source: 'next_data_query' };
        }
        if (nd && nd.props && nd.props.pageProps) {
            const pp = nd.props.pageProps;
            if (pp.serverResponse && pp.serverResponse.data && pp.serverResponse.data.id) {
                return {
                    id: pp.serverResponse.data.id,
                    source: 'next_data_response'
                };
            }
            if (pp.id) {
                return { id: pp.id, source: 'next_data_props' };
            }
        }
    } catch(e) {}

    // Try to extract from API URL in network logs (if available)
    try {
        const apiUrls = document.querySelectorAll('[data-api-url]');
        for (const el of apiUrls) {
            const url = el.getAttribute('data-api-url');
            const match = url.match(/\\/c\\/([a-zA-Z0-9-]+)/);
            if (match) {
                return { id: match[1], source: 'data_attr' };
            }
        }
    } catch(e) {}

    return null;
})()
"""

# Check if ChatGPT is still generating a response
IS_GENERATING_JS = """
(function() {
    // Check multiple indicators that ChatGPT is still generating

    // Check for stop button (indicates generating)
    const stopBtn = document.querySelector(
        '[data-testid="stop-button"], ' +
        'button[aria-label="Stop generating"], ' +
        'button[aria-label*="Stop"], ' +
        'button[aria-label*="stop"]'
    );
    if (stopBtn && stopBtn.offsetParent !== null) {
        return true;
    }

    // Check for streaming CSS class
    const streaming = document.querySelector('.result-streaming, [data-is-streaming="true"]');
    if (streaming) {
        return true;
    }

    // Check if send button is disabled (means generating)
    const sendBtn = document.querySelector(
        '[data-testid="send-button"], ' +
        'button[aria-label="Send"], ' +
        'button[aria-label*="send"]'
    );
    if (sendBtn && sendBtn.disabled) {
        return true;
    }

    // Check for loading spinner
    const spinner = document.querySelector('[class*="spinner"], [class*="loading"], .animate-spin');
    if (spinner && spinner.offsetParent !== null) {
        return true;
    }

    return false;
})()
"""

# Inject fetch interceptor for streaming capture
INJECT_FETCH_INTERCEPTOR_JS = """
(function() {
    if (window.__bridge_fetch_installed) return;
    window.__bridge_fetch_installed = true;

    const origFetch = window.fetch;
    window.fetch = async function(...args) {
        const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
        const response = await origFetch.apply(this, args);

        if (url.includes('/backend-api/conversation') || url.includes('/backend-api/chat')) {
            const clone = response.clone();
            try {
                const reader = clone.body.getReader();
                const decoder = new TextDecoder();

                (async () => {
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            const chunk = decoder.decode(value, { stream: true });
                            if (typeof window.bridge_on_chunk === 'function') {
                                window.bridge_on_chunk({ chunk: chunk, url: url });
                            }
                        }
                    } catch(e) {}
                })();
            } catch(e) {}
        }
        return response;
    };
})()
"""
