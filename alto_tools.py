#!/usr/bin/env python

""" alto_tools.py: simple methods to perform operations on ALTO xml files """

import argparse
import codecs
import io
import os
import re
import sys
from urllib import request
from imghdr import what
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path
import shutil

from PIL import Image
from tesserocr import PyTessBaseAPI, RIL, iterate_level
from tqdm import tqdm

if sys.stdout.encoding.lower() != 'utf-8':

    opts = {'encoding': 'utf-8', 'errors': 'surrogateescape', 'line_buffering': sys.stdout.line_buffering}
    sys.stdout = io.TextIOWrapper(sys.__stdout__.buffer, **opts)
    sys.stderr = io.TextIOWrapper(sys.__stderr__.buffer, **opts)

__version__ = '0.0.3'


def alto_parse(alto, **kargs):
    """ Convert ALTO xml file to element tree """
    try:
        xml = ET.parse(alto, **kargs)
    except ET.ParseError as e:
        print(f"Parser Error in file '{alto}': {e}")
    # Register ALTO namespaces
    # https://www.loc.gov/standards/alto/ | https://github.com/altoxml
    # alto-bnf (unofficial) BnF ALTO dialect - for further info see
    # http://bibnum.bnf.fr/alto_prod/documentation/alto_prod.html
    namespace = {'alto-1': 'http://schema.ccs-gmbh.com/ALTO',
                 'alto-2': 'http://www.loc.gov/standards/alto/ns-v2#',
                 'alto-3': 'http://www.loc.gov/standards/alto/ns-v3#',
                 'alto-4': 'http://www.loc.gov/standards/alto/ns-v4#',
                 'alto-bnf': 'http://bibnum.bnf.fr/ns/alto_prod'}
    # Extract namespace from document root
    if 'http://' in str(xml.getroot().tag.split('}')[0].strip('{')):
        xmlns = xml.getroot().tag.split('}')[0].strip('{')
    else:
        try:
            ns = xml.getroot().attrib
            xmlns = str(ns).split(' ')[1].strip('}').strip("'")
        except IndexError:
            sys.stderr.write(
                f'\nERROR: File "{alto.name}": no namespace declaration found.')
            xmlns = 'no_namespace_found'
    if xmlns in namespace.values():
        return alto, xml, xmlns
    else:
        sys.stdout.write(f'\nERROR: File "{alto.name}": namespace {xmlns} is not registered.\n')


def convert_bbox_to_areapos(x1, y1, x2, y2):
    """ Convert bbox values to area positions values """
    return (x1, y1, x2-x1, y2-y1)


def alto_redo_ocr(alto, xml, xmlns, lang, image, padding, filename, outputpath, gtline, text, confidence, confidence_threshold):
    """ Use bbox information and tesseract to redo ocr """
    # Find all <TextLine> elements+
    with PyTessBaseAPI(lang=lang, psm=7) as api:
        # Set necessary information
        lindex = 0
        fulltext_orig = ""
        fulltext = ""
        if gtline:
            linefolder = outputpath.joinpath('gtlines')
            linefolder.mkdir(exist_ok=True, parents=True)
        if text:
            textfolder = outputpath.joinpath('text')
            textfolder.mkdir(exist_ok=True, parents=True)
        for lines in xml.iterfind('.//{%s}TextLine' % xmlns):
            if gtline or text or confidence:
                textline_orig = ""
                wc = []
                for line in lines.findall('{%s}String' % xmlns):
                    # Check if there are no hyphenated words
                    wc.append(float(line.attrib.get('WC')))
                    if ('SUBS_CONTENT' not in line.attrib and 'SUBS_TYPE' not in line.attrib):
                        # Get value of attribute @CONTENT from all <String> elements
                        textline_orig += line.attrib.get('CONTENT') + ' '
                    else:
                        if ('HypPart1' in line.attrib.get('SUBS_TYPE')):
                            textline_orig += line.attrib.get('SUBS_CONTENT') + ' '
                            if ('HypPart2' in line.attrib.get('SUBS_TYPE')):
                                pass
                wc = round((sum(wc)/len(wc))*100, 2)
                textline_orig = textline_orig.strip()
            if confidence and confidence_threshold.isdigit() and wc < float(confidence_threshold):
                lindex += 1
                if text:
                    fulltext += textline_orig + '\n'
                    fulltext_orig += textline_orig + '\n'
                continue
            # New line after every <TextLine> element
            x1, y1, = int(lines.attrib['HPOS']), int(lines.attrib['VPOS'])
            x2, y2 = x1+int(lines.attrib['WIDTH']), y1+int(lines.attrib['HEIGHT'])
            if padding:
                if x1-padding[0] >= 0 and y1-padding[1] >= 0 \
                        and x2+padding[2] <= image.width and y2+padding[3] <= image.height:
                    x1, y1, x2, y2 = x1-padding[0], y1-padding[1], x2+padding[2], y2+padding[3]
            line_img = image.crop((x1, y1, x2, y2))
            api.SetImage(line_img)
            api.Recognize()
            ri = api.GetIterator()
            for word in lines.findall('*'):
                lines.remove(word)
            hpos, vpos, width, height = 0, 0, 0, 0
            tailstr = lines.tail+'\t'
            for line in iterate_level(ri, RIL.TEXTLINE):
                string_index = 0
                if not line.Empty(RIL.TEXTLINE):
                    if gtline:
                        line_img.save(linefolder.joinpath(filename.with_suffix('').name+f'_line_{lindex:04d}.png'))
                        with open((linefolder.joinpath(filename.with_suffix('').name+f'_line_{lindex:04d}.orig.gt.txt')), 'w') as fout:
                            fout.write(textline_orig)
                        with open((linefolder.joinpath(filename.with_suffix('').name+f'_line_{lindex:04d}.gt.txt')), 'w') as fout:
                            fout.write(line.GetUTF8Text(RIL.TEXTLINE).strip())
                        lindex += 1
                    if text:
                        fulltext += line.GetUTF8Text(RIL.TEXTLINE).strip()+'\n'
                        fulltext_orig += textline_orig+'\n'
                    for word in iterate_level(line, RIL.WORD):
                        content = word.GetUTF8Text(RIL.WORD).strip()
                        wc = word.Confidence(RIL.WORD)# r == ri
                        lx1, ly1, lx2, ly2 = word.BoundingBoxInternal(RIL.WORD)
                        if any((hpos, vpos, width, height)):
                            el = ET.XML(f'<SP WIDTH="{x1+lx1-(hpos+width)}" VPOS="{vpos}" HPOS="{hpos+width}"/>')
                            el.tail = tailstr
                            lines.append(el)
                        hpos, vpos, width, height = convert_bbox_to_areapos(x1+lx1, y1+ly1, x1+lx2, y1+ly2)
                        el = ET.XML(f'<String ID="string_{string_index}" '
                                            f'HPOS="{hpos}" VPOS="{vpos}" WIDTH="{width}" HEIGHT="{height}" '
                                            f'WC="{wc/100:.4f}" CONTENT="{escape(content)}"/>')
                        el.tail = tailstr
                        lines.append(el)
                        string_index += 1
        if text:
            with open((textfolder.joinpath(filename.with_suffix('').name+f'.orig.txt')), 'w') as fout1:
                fout1.write(fulltext_orig)
            with open((textfolder.joinpath(filename.with_suffix('').name+f'.txt')), 'w') as fout2:
                fout2.write(fulltext)


def checkURL(url):
    """This function checks the validity of URLs."""
    try:
        req = request.Request(url, method='HEAD')
        res = request.urlopen(req)
        if res.getcode() == 200:
            return True
    except:
        return False
    return False


def load_image(xml, xmlns, filename, imagepath):
    """ Load the image from file or url"""
    if imagepath == "" or imagepath.startswith(("https://", "http://", "@https://", "@http://")):
        try:
            if imagepath == "":
                for imagefile in xml.iterfind('.//{%s}sourceImageInformation' % xmlns):
                    imagename = imagefile.find('{%s}fileName' % xmlns).text
                    if os.path.isfile(imagename):
                       return Image.open(imagename)
                    elif checkURL(imagename):
                        return Image.open(request.urlopen(imagename))
            elif imagepath.startswith('@'):
                if checkURL(imagepath[1:]):
                    return Image.open(request.urlopen(imagepath[1:]))
            else:
                for ext in ['.jpg', '.png', '.tiff', '.tif', '.jp2']:
                    imagename = imagepath.strip('/')+"/"+filename.with_suffix('').name+ext
                    if checkURL(imagename):
                        return Image.open(request.urlopen(imagename))
        except:
            print("Could not find image filename in xml file")
            return None
    else:
        filename = Path(filename)
        if imagepath.startswith('@'):
            if os.path.isfile(imagepath[1:]):
                return Image.open(imagepath[1:])
        elif imagepath.startswith('..'):
            imagepath = imagepath.split('..')
            for _ in range(0, len(imagepath)):
                filename = filename.parent
            imagepath = filename.joinpath(imagepath[-1][1:])
        elif imagepath.startswith('.'):
            if imagepath in ['.', './']:
                imagepath = filename.parent
            else:
                imagepath = filename.parent.joinpath(imagepath[2:])
        else:
            imagepath = Path(imagepath)
        for fname in imagepath.rglob(filename.with_suffix('').name+'*'):
            if what(fname):
                return Image.open(fname)
    return None


def get_padding(paddingstr):
    if paddingstr.isdigit(): return [int(paddingstr)]*4
    paddingstrs = paddingstr.split(',')
    if len(paddingstrs) == 4:
        return [int(paddingval) for paddingval in paddingstrs]
    return None


def alto_text(xml, xmlns):
    """ Extract text content from ALTO xml file """
    # Ensure use of UTF-8
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding != 'UTF-8':
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    # Find all <TextLine> elements
    for lines in xml.iterfind('.//{%s}TextLine' % xmlns):
        # New line after every <TextLine> element
        sys.stdout.write('\n')
        # Find all <String> elements
        for line in lines.findall('{%s}String' % xmlns):
            # Check if there are no hyphenated words
            if ('SUBS_CONTENT' not in line.attrib and 'SUBS_TYPE' not in line.attrib):
            # Get value of attribute @CONTENT from all <String> elements
                text = line.attrib.get('CONTENT') + ' '
            else:
                if ('HypPart1' in line.attrib.get('SUBS_TYPE')):
                    text = line.attrib.get('SUBS_CONTENT') + ' '
                    if ('HypPart2' in line.attrib.get('SUBS_TYPE')):
                        pass
            sys.stdout.write(text)


def alto_illustrations(xml, xmlns):
    """ Extract bounding boxes of illustration from ALTO xml file """
    # Find all <Illustration> elements
    for illustration in xml.iterfind('.//{%s}Illustration' % xmlns):
        # Get @ID of <Illustration> element
        illustration_id = illustration.attrib.get('ID')
        # Get coordinates of <Illustration> element
        illustration_coords = (illustration.attrib.get('HEIGHT') + ','
                            + illustration.attrib.get('WIDTH') + ','
                            + illustration.attrib.get('VPOS') + ','
                            + illustration.attrib.get('HPOS'))
        sys.stdout.write('\n')
        illustrations = illustration_id + '=' + illustration_coords
        sys.stdout.write(illustrations)


def alto_confidence(alto, xml, xmlns):
    """ Calculate word confidence for ALTO xml file """
    score = 0
    count = 0
    # Find all <String> elements
    for conf in xml.iterfind('.//{%s}String' % xmlns):
        # Get value of attribute @WC (Word Confidence) of all <String> elements
        wc = conf.attrib.get('WC')
        # Calculate sum of all @WC values as float
        if wc is not None:
            score += float(wc)
            # Increment counter for each word
            count += 1
    # Divide sum of @WC values by number of words
    if count > 0:
        confidence = score / count
        result = round(100 * confidence, 2)
        sys.stdout.write(f'\nFile: {alto.name}, Confidence: {result}')
        return result
    else:
        sys.stdout.write(f'\nFile: {alto.name}, Confidence: 00.00')
        return 0


def write_output(alto, output, args):
    """ Write output to file(s) instead of stdout """
    if len(output) == 0:
        sys.stdout.write()
    else:
        if args.text:
            output_filename = alto.name + '.txt'
            sys.stdout = open(output_filename, 'w')
            sys.stdout.write('writing output file: ' + alto.name + '.txt')
        if args.illustrations:
            output_filename = alto.name + '.img.txt'
            sys.stdout = open(output_filename, 'w')
            sys.stdout.write('writing output file: ' + alto.name + '.img.txt')
        if args.confidence:
            output_filename = alto.name + '.conf.txt'
            sys.stdout = open(output_filename, 'w')
            sys.stdout.write('writing output file: ' + alto.name + '.conf.txt')


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="ALTO Tools: simple methods to perform operations on ALTO xml files",
        add_help=True,
        prog='alto_tools.py',
        usage='python %(prog)s INPUT [options]')
    parser.add_argument('INPUT',
                        nargs='+',
                        help='path to ALTO file (add a * to the end of the path for recursive search)')
    parser.add_argument('-o', '--output',
                        default='',
                        dest='output',
                        help='path to output directory (if none specified the altofolder is used)')
    parser.add_argument('-v', '--version',
                        action='version',
                        version=__version__,
                        help='show version number and exit')
    parser.add_argument('-c', '--confidence',
                        action='store_true',
                        default=False,
                        dest='confidence',
                        help='calculate OCR page confidence from ALTO file')
    parser.add_argument('-t', '--text',
                        action='store_true',
                        default=False,
                        dest='text',
                        help='extract text content from ALTO file can be used with reocr to extract the original text'
                             'and the new one')
    parser.add_argument('-r', '--reocr',
                        action='store_true',
                        default=False,
                        dest='reocr',
                        help='Redo ocr based on the alto bbox with tesseract')
    parser.add_argument('--lang',
                        default='eng',
                        dest='lang',
                        help='Use the language model from tesseract')
    parser.add_argument('--padding',
                        default='0',
                        dest='padding',
                        help='Extra padding around the bbox '
                             '(e.g. "8,3,6,3" = "left, up, right, down" or "5" = 5 to all direction')
    parser.add_argument('--imagepath',
                        default='',
                        dest='imagepath',
                        help='Path to images (default: use image-filename in the sourceImageInformation section).'
                             'With an @ in front it points directly to the imagefile, eg. @./{filename}.jpg')
    parser.add_argument('--backup',
                        action='store_true',
                        default=False,
                        dest='backup',
                        help='Backup original xml file')
    parser.add_argument('--gtline',
                        action='store_true',
                        default=False,
                        dest='gtline',
                        help='Stores each line image (.png) and the old text (.old.gt.txt) and the new text (.gt.txt)')
    parser.add_argument('--confidence-threshold',
                        default="60.00",
                        dest='confidence_threshold',
                        help='If confidence is active only a line with lower confidence values will be reocrd')
    parser.add_argument('-l', '--illustrations',
                        action='store_true',
                        default=False,
                        dest='illustrations',
                        help='extract bounding boxes of illustrations from ALTO file')
    parser.add_argument('-E', '--xml-encoding',
                        dest='xml_encoding',
                        default=None,
                        help='XML encoding')
    parser.add_argument('--file-encoding',
                        dest='file_encoding',
                        default='UTF-8',
                        help='File encoding')
    args = parser.parse_args()
    return args


def walker(inputs, output):
    """
    Returns all file names in inputs, and recursively for directories.

    If an input is
    - a file:      return as is
    - a directory: return all files in it, recursively, filtered by fnfilter.
    """
    for i in inputs:
        if i.startswith(('http://', 'https://')) and checkURL(i):
            outputfolder = Path('.') if output == "" else Path(output)
            outputfolder.mkdir(exist_ok=True)
            xmlpath = outputfolder.joinpath(i.rsplit("/", 1)[1])
            with open(outputfolder.joinpath(i.rsplit("/", 1)[1]), "wb") as fout:
                fout.write(request.urlopen(i).read())
            yield xmlpath
        if os.path.isfile(i):
            yield Path(i)
        elif i.endswith('*'):
            for file in Path(i[:-1]).rglob('*'):
                if file.suffix in ['.xml', '.alto']:
                    yield file
        else:
            for file in Path(i).glob('*'):
                if file.suffix in ['.xml', '.alto']:
                    yield file

def main():
    if sys.version_info < (3, 0):
        sys.stdout.write('Python 3 is required.\n')
        sys.exit(-1)

    args = parse_arguments()
    if not len(sys.argv) > 2:
        sys.stdout.write('\nNo operation specified, ')
        os.system('python alto_tools.py -h')
        sys.exit(-1)
    else:
        confidence_sum = 0
        number_of_files = 0
        for number_of_files, filename in tqdm(enumerate(walker(args.INPUT, args.output))):
            try:
                if args.xml_encoding:
                    xml_encoding = args.xml_encoding
                    if xml_encoding == 'auto':
                        with open(filename, 'rb') as f:
                            m = re.search('encoding="(.*?)"', f.read(45).decode('utf-8'))
                            xml_encoding = m.group(1)
                    xmlp = ET.XMLParser(encoding=xml_encoding)
                    alto, xml, xmlns = alto_parse(filename, parser = xmlp)
                else:
                    with open(filename, 'r',  encoding=args.file_encoding) as alto:
                        alto, xml, xmlns = alto_parse(alto)
            except IndexError:
                continue
            except ET.ParseError as e:
                print("Error parsing %s" % str(filename.resolve()), file=sys.stderr)
                raise(e)
            outpufolder = filename.parent.joinpath(filename.name.split('.', 1)[0].replace('.', '_')) if args.output != "" else Path(args.output).joinpath(filename.name.split('.', 1)[0].replace('.', '_'))

            if args.reocr:
                image = load_image(xml, xmlns, filename, args.imagepath)
                padding = get_padding(args.padding)
                if image:
                    alto_redo_ocr(alto, xml, xmlns, args.lang, image,padding, filename, outpufolder,
                                  args.gtline, args.text, args.confidence, args.confidence_threshold)
                    if args.backup:
                        outpufolder.mkdir(exist_ok=True)
                        backupfolderfile = outpufolder.joinpath(filename.name)
                        backupfolderfile.unlink(missing_ok=True)
                        shutil.move(str(filename.resolve()), str(outpufolder.resolve()))
                    ET.register_namespace('', xmlns)
                    xml.write(str(filename.resolve()), encoding='utf-8', xml_declaration=True)
            else:
                if args.confidence:
                    confidence_sum += alto_confidence(alto, xml, xmlns)
                if args.text:
                    alto_text(xml, xmlns)
                if args.illustrations:
                    alto_illustrations(xml, xmlns)
        if number_of_files >= 2:
            print(
                f"\n\nConfidence of folder: {round(confidence_sum/number_of_files, 2)}")


if __name__ == "__main__":
    main()
