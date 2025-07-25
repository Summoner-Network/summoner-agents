# Notion API

Create an agent here: [here](https://www.notion.so/my-integrations)

API docs [here](https://github.com/ramnes/notion-sdk-py)


## Create a Notion Integration and Get Your Token

1. Go to https://www.notion.com/my-integrations
2. Click **"New integration"**
3. Give it a name and select the appropriate workspace
4. Save the integration and copy the **Internal Integration Token**

Paste it into your `.env` file:

```env
NOTION_TOKEN=your_secret_token_here
```


## Get the Page ID from the Notion URL

A Notion page URL looks like this:

```
https://www.notion.so/White-paper-version-0-2-1fe996a6195e805684fbcbbc8f335476
```

The Page ID is the last **32-character string**, possibly separated by dashes. To get the correct format:

1. Extract the raw ID from the URL:

   ```
   1fe996a6195e805684fbcbbc8f335476
   ```
2. Insert dashes to match UUID format:

   ```
   1fe996a6-195e-8056-84fb-cbbc8f335476
   ```

Put this in your `.env` file:

```env
NOTION_PAGE_ID=1fe996a6-195e-8056-84fb-cbbc8f335476
```


## Notion Page parsing: Supported Block Types

The Notion API returns a list of **blocks** that make up your page. Each block has a type. Here are common block types you might encounter:

| Block Type           | Description                              |
| -------------------- | ---------------------------------------- |
| `paragraph`          | Plain text content                       |
| `heading_1`          | Top-level heading (`#` in Markdown)      |
| `heading_2`          | Second-level heading (`##`)              |
| `heading_3`          | Third-level heading (`###`)              |
| `bulleted_list_item` | Bullet points                            |
| `numbered_list_item` | Numbered list items                      |
| `to_do`              | Checkbox task item                       |
| `toggle`             | Expandable/collapsible section           |
| `callout`            | Block with icon, used for notes/warnings |
| `code`               | Syntax-highlighted code block            |
| `quote`              | Quoted text                              |
| `divider`            | Horizontal line                          |
| `image`, `video`     | Media embeds                             |
| `child_page`         | A linked sub-page                        |
| `equation`           | LaTeX-style math equations               |

⚠️ **Note**: Some blocks (like toggles or list items) can have their own nested children. To retrieve them, you must call `blocks.children.list` again with the child block's ID.

