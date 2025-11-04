Step-by-Step Guide:

Prepare the inputURLS.csv File:

Create a new CSV file called inputURLS.csv (or use the one that is in place) in the same directory as the executable file.
Open the inputURLS.csv file using a text editor or spreadsheet application.
Input URL and Filename Pairs:

In the inputURLS.csv file, enter the URL and filename pairs manually.
Each URL and filename pair should be placed on a separate line, separated by a comma.
For example:
https://www.imot.bg/pcgi/imot.cgi?act=3&slink=9hclfa&f1=1&fe7=1,KrastovaOutput.csv
https://www.imot.bg/pcgi/imot.cgi?act=3&slink=9hd743&f1=1&fe7=1,ManastirskiOutput.csv

Save the inputURLS.csv File:
*Have in mind that the URLS Should be from search in Imot.bg and when you navigate to page 2. Then change "f1=2" to be "f1=1"

After entering all the URL and filename pairs, save the inputURLS.csv file.
Execute the Script (Executable File): ImotBg

Locate the executable file (exe) in the directory where it was generated.
Double-click the executable file to run it.
Web Scraping Execution:

The script will read the URLs and filenames from the inputURLS.csv file and perform the web scraping process for each URL and corresponding output filename.
The web scraping logic should be implemented in the script file that was converted to an executable, and it should include libraries like requests and BeautifulSoup for scraping the web pages.
The exact scraping operations will depend on your specific requirements and the structure of the target websites.
Output Files:

After the web scraping process is complete, the script will generate output files based on the specified filenames in the inputURLS.csv file.
The output files will contain the scraped data in the desired format (e.g., CSV format) according to your scraping logic.
The output files will be created in the same directory as the executable file.
Please note that the provided guide assumes you have already manually entered the URL and filename pairs into the inputURLS.csv file. It covers the execution of the executable file and the basic workflow of reading the URLs from the file and performing web scraping operations.

Remember to adjust the web scraping logic in your script file based on your specific requirements, including libraries such as requests and BeautifulSoup as needed.