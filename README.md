# alto-tools

> [Python3](https://www.python.org/) script for performing various operations on [ALTO](http://www.loc.gov/standards/alto/) files.

## Usage
It is now possible to use an url to an existing alto file instead of a locally existing filepaths.

### Basic usage
* extract UTF-8 text content from ALTO file

  `python3 alto_tools.py alto.xml -t`

* extract page OCR confidence score from ALTO file

  `python3 alto_tools.py alto.xml -c`

* extract bounding boxes of illustrations from ALTO file

  `python3 alto_tools.py alto.xml -l`

### ReOCR
  This function uses the existing line layout parameters to make a new line recognition process with the
  tesseract ocr-engine and overwrite the existing text content.

  * start the reocr process

    `python3 alto-tools.py alto.xml -r`

####  Options to extend and tweak the reOCR process
  * add path/url to image-files, if the url points directly to file you need to add an @ in front of the url.

    `{reocr-cmd} --imagepath /{path}/{to}/{images}`

  * save image file in output folder

    `{reocr-cmd} --save-image`

  * padding pixel to the line-image with either four values or one value for all four
    directions

    `{reocr-cmd} --padding 8,3,6,3`

    `{reocr-cmd} --padding 5`
  * create a backup of the original version

    `{reocr-cmd} --backup`
  * create of each line an image and a text file for the original and the new version

      `{reocr-cmd} --gtline`
  * create a textfile containing the original and one containing the new text content

    `{reocr-cmd} -t`
  * only do the reocr for lines which are lower than the threshold (default: 60 %)

    `{reocr-cmd} -c`
  * set the confidence threshold e.g. 90 for 90 %

    `{reocr-cmd} -c --confidence-threshold 90`

## Planned

* write output to file(s) - currently all output is sent to `stdout`

  `python3 alto-tools.py alto.xml [OPTION] -o`
