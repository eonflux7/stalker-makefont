import os
import xmltodict

for file in os.listdir("./"):
    if file.endswith(".fnt"):
        with open(file) as xmlFile:
            ghost_struct = [{'@id':str(k),'@x':'0','@y':'0','@width':'0','@height':'0'} for k in range(256)]
            data = xmltodict.parse(xmlFile.read())
            chars = data['font']['chars']['char']

            ini = '[width_correction]\nvalue = -1.0\n\n[symbol_coords]\nheight = ' + data['font']['info']['@size'] + '\n'

            for i in ghost_struct:
                # cycle thru available chars
                for j in chars:
                    if i['@id'] == j['@id']:
                        i['@x'] = j['@x']
                        i['@y'] = j['@y']
                        i['@width'] = j['@width']
                        i['@height'] = j['@height']
                id = int(i['@id'])
                sid = i['@id']
                x = int(i['@x'])
                y = int(i['@y'])
                wcoord = int(x + int(i['@width']))
                hcoord = int(y + int(i['@height']))

                if id < 10:
                    ini += f'00{sid} = {x}, {y}, {wcoord}, {hcoord}\n'
                elif id >= 10 and id < 100:
                    ini += f'0{sid} = {x}, {y}, {wcoord}, {hcoord}\n'
                elif id >= 100:
                    ini += f'{sid} = {x}, {y}, {wcoord}, {hcoord}\n'

            f = open(os.path.splitext(file)[0]+'.ini', "w")
            f.write(ini)
            f.close()

for file in os.listdir("./"):
    if file.endswith(".fnt"):
        os.remove(file)
        continue 

    name = os.path.splitext(file)[0]
    ext = os.path.splitext(file)[1]

    if name == 'graff19' or name == 'graff19_0':
        os.rename(file, f'ui_font_graff_19_1600{ext}')
    elif name == 'graff22' or name == 'graff22_0':
        os.rename(file, f'ui_font_graff_22_1600{ext}')
    elif name == 'sans16' or name == 'sans16_0':
        os.rename(file, f'ui_font_letter_16_1600{ext}')
    elif name == 'sans18' or name == 'sans18_0':
        os.rename(file, f'ui_font_letter_18_1600{ext}')
