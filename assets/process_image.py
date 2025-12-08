from PIL import Image, ImageDraw

def round_corners_with_border(input_path, output_path, radius=50, border_size=10, border_color=(128, 128, 128, 255)):
    """Rounds the corners of an image and adds a solid grey border."""
    img = Image.open(input_path).convert("RGBA")  # Ensure transparency support
    width, height = img.size

    # Increase canvas size to fit the border
    new_width, new_height = width + 2 * border_size, height + 2 * border_size
    bordered_img = Image.new("RGBA", (new_width, new_height), border_color)  # Start with solid grey

    # Create a transparent mask with rounded corners for the border
    border_mask = Image.new("L", (new_width, new_height), 0)
    draw = ImageDraw.Draw(border_mask)
    draw.rounded_rectangle((0, 0, new_width, new_height), radius=radius + border_size, fill=255)

    # Apply the border mask (ensures correct transparency)
    bordered_img.putalpha(border_mask)

    # Create another mask for the **inner image**
    inner_mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(inner_mask)
    draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)

    # Ensure the inner image is pasted at the correct size
    img = img.resize((width, height))  # Ensure it matches expected dimensions

    # Paste the original image onto the bordered image with the inner mask
    bordered_img.paste(img, (border_size, border_size), inner_mask)

    # Save the final image
    bordered_img.save(output_path, format="PNG")

# Usage
for s in ["assets/img/slack_summoner_default"]:
    round_corners_with_border(f"{s}.png", f"{s}_rounded.png", radius=50, border_size=4)