import requests
from bs4 import BeautifulSoup
import csv
import os.path

# Read the URLs and filenames from the inputURLS.csv file
urls = []

with open("inputURLS.csv", "r") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        url = row["URL"]
        filename = row["FileName"]
        urls.append((url, filename))

# Iterate over each URL and corresponding output filename
for url, output_filename in urls:
    reference_data = {}  # Dictionary to store the reference data from the CSV file
    processed_keys = set()  # Set to store the processed reference keys
    new_records = []  # List to store new or changed records

    # Check if the output file exists
    file_exists = os.path.isfile(output_filename)

    # Read the CSV file and populate the reference_data dictionary
    if file_exists:
        with open(output_filename, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, None)  # Get the header row
            if header is not None:
                if len(header) == 3 and header[0] == "RecordId" and header[1] == "Price" and header[2] == "oldValue":
                    for row in reader:
                        reference_key = row[0]
                        reference_value = row[1]
                        reference_data[reference_key] = reference_value
                        processed_keys.add(reference_key)

    # Clean the output file
    with open(output_filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["RecordId", "Price", "oldValue"])

    # Open the CSV file in append mode with UTF-8 encoding
    with open(output_filename, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)

        record_id = 1
        page = 1
        totalPages = 10

        while True:
            if page > int(totalPages):
                break

            # Build the URL with the current page number
            page_url = url.replace("f1=1", "f1=" + str(page))

            response = requests.get(page_url)
            html_content = response.content

            soup = BeautifulSoup(html_content, "html.parser")

            if page == 1:
                totalPages = soup.find_all("span", {"class": "pageNumbersInfo"})
                span = totalPages[0]  # Accessing the first span
                text = span.get_text()
                prefix = "Страница 1 от "
                totalPages = text[len(prefix):].strip()

            tables = soup.find_all(
                "table",
                style="margin-bottom:0px; border-top:#990000 1px solid; background:url(../images/picturess/top_bg.gif); background-position:bottom; background-repeat:repeat-x;",
            )
            tables += soup.find_all(
                "table",
                style="margin-bottom:0px; border-top:#990000 1px solid; background:url(../images/picturess/vip_bg.gif); background-position:bottom; background-repeat:repeat-x;",
            )
            tables += soup.find_all(
                "table",
                style="margin-bottom:0px; border-top:#990000 1px solid;",
            )

            for table in tables:
                div_with_price = table.find("div", {"class": "price"})
                if div_with_price is None:
                    continue
                price_text = div_with_price.text.strip()

                first_a = table.find("a", {"class": "lnk1"})
                if first_a is None:
                    continue
                first_a_href = first_a.get("href")
                first_a_href = first_a_href.split("&slink")[0]

                # Remove leading "//" from first_a_href
                if first_a_href.startswith("//"):
                    first_a_href = first_a_href[2:]  # Remove the first two characters

                # Check if the reference key exists in the reference_data dictionary
                if first_a_href in reference_data:
                    reference_value = reference_data[first_a_href]
                    # Compare the value in Column B with the reference value
                    if price_text != reference_value:
                        new_value = reference_value  # Use price_text as the new value
                        new_records.append([first_a_href, price_text, new_value])  # Store the new record
                        print("Record with changed price: "+first_a_href+"; With price: "+new_value+"->"+price_text)
                    else:
                        new_value = ""  # No change, leave the new value empty
                    del reference_data[first_a_href]  # Remove the reference key from the dictionary
                    processed_keys.add(first_a_href)  # Add the processed reference key to the set
                else:
                    reference_value = ""  # Missing reference, leave the reference value empty
                    new_value = "new"  # Missing reference, use 'new' as the new value
                    new_records.append([first_a_href, price_text, new_value])  # Store the new record
                    print("New record: "+first_a_href+"; With price: "+price_text)

                # Check if the line needs to be written

                writer.writerow([first_a_href, price_text, new_value])

                record_id += 1

            page += 1

    # Process the remaining reference_data entries (missing references)
    with open(output_filename, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        for reference_key in reference_data.keys():
            if reference_key not in processed_keys:
                writer.writerow([reference_key, reference_data[reference_key], "new"])
                print("New record: "+reference_key+"; With price: "+reference_data[reference_key])
    # Create a new file for new or changed records
    new_records_filename = "NewRecords_" + output_filename
    if new_records:   
        with open(new_records_filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["RecordId", "Price", "oldValue"])
            writer.writerows(new_records)
    else:
        try:
            os.remove( new_records_filename )
            print(f"There are no new recrods for: "+output_filename)
        except OSError as e:
            print(f"There are no new recrods for: "+output_filename)

input("Press Enter to exit...")