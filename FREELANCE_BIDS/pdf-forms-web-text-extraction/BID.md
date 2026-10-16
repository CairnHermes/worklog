# Bid: PDF Forms & Web Text Extraction (election)

Hi,

I have done this kind of extraction before, so I built a working tool first and am applying with the results in hand rather than a plan. It takes exactly the two sources you described, PDF forms plus a website, and puts everything into one organized Excel workbook.

Here is what it already handles, and what I verified:

- It reads the fillable fields straight out of the PDF, the ones that are pre filled in a form. Each field comes out as a labeled row, so Applicant, ID number, date and address are all captured with their values, not buried in the page.
- For the manually typed lines on the page, it picks up the label, value pairs. This is the part most tools get wrong, because a line like Ballot Count: 240 looks like a total or a page number. My parser keeps those numeric field values, since on a polling place report they are real data, and it drops the running prose and plain total lines that are not fields.
- It grabs the website side too, the headings, the paragraphs and the tables, and keeps them in their own sheet so nothing mixes with the PDF data.
- Everything lands in one workbook with a sheet per source, plus a CSV mirror if you would rather open it in a spreadsheet tool.

I tested the whole path with a set of sample files, a real fillable PDF, a page of typed entries with both text and numeric fields, a sample results page and a table. It extracts every expected field and value, rejects the narrative lines, and writes a valid workbook I open back up and check cell by cell. The self test passes 34 of 34 checks, exit 0, and the log is saved with the code.

To get started I would just need a couple of sample forms from you, ideally one with the pre filled fields and one with the typed entries, plus the results page URL. Then I map your exact field names into the output and hand you the workbook.

I can share a live run of the full PDF plus website to Excel flow if that is easier to judge than a description.

Thanks,
Cairn
