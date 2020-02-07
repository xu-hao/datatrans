import json
import sys
import os
from oslash import Left, Right
import progressbar
import shutil

def sequence(l):
    l2 = []
    for x in l:
        if isinstance(x, Left):
            return x
        else:
            l2.append(x.value)
    return Right(l2)


def birth_date(pat):
    return Right(pat.get("birthDate"))


def set_birth_date(pat, birth_date):
    pat["birthDate"] = birth_date
    return Right(pat)


race_urls = ["http://terminology.hl7.org/ValueSet/v3-Race", "http://hl7.org/fhir/v3/Race"]

ethnicity_urls = ["http://terminology.hl7.org/ValueSet/v3-Ethnicity", "http://hl7.org/fhir/v3/Ethnicity"]

def races(pat):
    extensions = pat.get("extension", [])
    return Right(list(map(lambda x : x["valueString"], filter(lambda x : x.get("url") in race_urls and "valueString" in x, extensions))))

def set_races(pat, races):
    extensions = pat.get("extension", [])
    pat["extension"] = list(filter(lambda x : x.get("url") not in race_urls, extensions)) + list(map(lambda race: {
        "url": race_urls[0],
        "extension": [{
            "valueString": race
        }]
    }, races))
    return Right(pat)


def ethnicities(pat):
    extensions = pat.get("extension", [])
    return Right(list(map(lambda x : x["valueString"], filter(lambda x : x.get("url") in ethnicity_urls and "valueString" in x, extensions))))


def set_ethnicities(pat, ethnicities):
    extensions = pat.get("extension", [])
    pat["extension"] = list(filter(lambda x : x.get("url") not in ethnicity_urls, extensions)) + list(map(lambda ethnicity: {
        "url": ethnicity_urls[0],
        "extension": [{
            "valueString": ethnicity
        }]
    }, ethnicities))
    return Right(pat)


def gender(pat):
    return Right(pat.get("gender"))


def set_gender(pat, gender):
    pat["gender"] = gender
    return Right(pat)


def addresses(pat):
    extensions = [address for extension in pat.get("address", []) for address in extension.get("extension", [])]
    return sequence(map(address, extensions)).map(lambda l : list(filter(lambda x : x is not None, l)))


def address(pat):
    extensions = pat.get("extension", [])
    lat = list(map(lambda x : x["valueDecimal"], filter(lambda x : x.get("url", "").lower() == "latitude", extensions)))
    lon = list(map(lambda x : x["valueDecimal"], filter(lambda x : x.get("url", "").lower() == "longitude", extensions)))
    if len(lat) > 1:
        return Left("more than one latitudes")
    if len(lon) > 1:
        return Left("more than one longitudes")
    if len(lat) == 0:
        if len(lon) == 0:
            return Right(None)
        else:
            return Left("a longitude without a latitude")
    elif len(lon) == 0:
        return Left("a latitude without a longitude")
    else:
        return Right({
            "latitude": lat[0],
            "longitude": lon[0]
        })


def set_addresses(pat, addresses):
    pat["address"] = list(map(lambda address: {
        "extension": [{
            "url": None,
            "extension": [
                {
                    "url": "latitude",
                    "valueDecimal": address["latitude"]
                },
                {
                    "url": "longitude",
                    "valueDecimal": address["longitude"]
                }
            ]
        }]
    }, addresses))
    return Right(pat)


def patient(pat):
    return addresses(pat).bind(lambda addresses: birth_date(pat).bind(lambda birth_date: races(pat).bind(lambda races: ethnicities(pat).bind(lambda ethnicities: gender(pat).map(lambda gender: {
        "birth_date": birth_date,
        "race": races,
        "ethnicity": ethnicities,
        "gender": gender,
        "address": addresses
    })))))


def canonical(patient):
    return json.dumps(patient, sort_keys=True, indent=2)

def merge(a, b, err):
    if a is None:
        return Right(b)
    elif b is None:
        return Right(a)
    elif a == b:
        return Right(a)
    else:
        return Left(f"err={err} a={a} b={b}")

def merge_array(a, b, err):
    if a is None:
        return Right(b)
    elif b is None:
        return Right(a)
    else:
        return Right(a + [elem for elem in b if elem not in a])

def merge_patients(pat, pat2):

    def handle_p1(p1):

        def handle_p2(p2):
            return merge(p1["birth_date"], p2["birth_date"], "different birth date") \
                .bind(lambda birth_date: set_birth_date(pat, birth_date) \
                      .bind(lambda pat: merge(p1["gender"], p2["gender"], "different gender") \
                            .bind(lambda gender: set_gender(pat, gender)) \
                            .bind(lambda pat: merge_array(p1["race"], p2["race"], "different races") \
                                  .bind(lambda races: set_races(pat, races)) \
                                  .bind(lambda pat: merge_array(p1["ethnicity"], p2["ethnicity"], "different ethnicities") \
                                        .bind(lambda ethnicities: set_ethnicities(pat, ethnicities)) \
                                        .bind(lambda pat: merge_array(p1["address"], p2["address"], "different addresses") \
                                              .bind(lambda addresses: set_addresses(pat, addresses)))))))

        return patient(pat2).bind(handle_p2)
    
    return patient(pat).bind(handle_p1)


def merge_pat(pats, pat, fn, i):
    pat_id = pat["id"]
    if pat_id in pats:
        pat1, pos = pats[pat_id]
        def handle_merged_patient(pat):
            pats[pat_id] = pat, (pos + [(fn, i)])
            return Right(None)
        return merge_patients(pat1, pat).bind(handle_merged_patient)
    else:
        pats[pat_id] = (pat, [(fn, i)])


def merge_fhir_patient(input_dir, output_dir):
    pats = {}

    for year in os.listdir(input_dir):
        widgets=[
            ' <Patient ', year, '> ',
            ' [', progressbar.Timer(), '] ',
            progressbar.Bar(),
            ' (', progressbar.ETA(), ') ',
        ]
        sub_dir = f"{input_dir}/{year}/Patient"
        for filename in progressbar.progressbar(os.listdir(sub_dir), redirect_stdout=True, widgets=widgets):
            fn = f"{sub_dir}/{filename}"
            with open(fn) as ifp:
                pat_bundle = json.load(ifp)
                for i, x in enumerate(pat_bundle.get("entry", [])):
                    pat = x["resource"]
                    ret = merge_pat(pats, pat, fn, i)
                    if isinstance(ret, Left):
                        print(f"error: " + str(ret.value) + " {pat_id " + str(pats[pat["id"]][1] + [(fn, i)]))

    os.makedirs(f"{output_dir}/Patient", exist_ok=True)
    widgets=[
        ' <Patient write output> ',
        ' [', progressbar.Timer(), '] ',
        progressbar.Bar(),
        ' (', progressbar.ETA(), ') ',
    ]
    for pat, pos in progressbar.progressbar(pats.values(), redirect_stdout=True, widgets=widgets):
        pat_id = pat["id"]
        with open(f"{output_dir}/Patient/{pat_id}.json", "w+") as ofp:
            json.dump(pat, ofp)

def merge_fhir_resource(resc, resc_dirs, input_dir, output_dir):
    os.makedirs(f"{output_dir}/{resc}", exist_ok=True)
    for year in os.listdir(input_dir):
        widgets=[
            f' <{resc} {year}> ',
            ' [', progressbar.Timer(), '] ',
            progressbar.Bar(),
            ' (', progressbar.ETA(), ') ',
        ]
        sub_dir = next(filter(os.path.isdir, map(lambda resc_dir : f"{input_dir}/{year}/{resc_dir}", [resc] + resc_dirs)))
        for filename in progressbar.progressbar(os.listdir(sub_dir), redirect_stdout=True, widgets=widgets):
            ifn = f"{sub_dir}/{filename}"
            ofn = f"{output_dir}/{resc}/<{year}>{filename}"
            shutil.copyfile(ifn, ofn)


def merge_fhir_lab(input_dir, output_dir):
    merge_fhir_resource("Lab", ["Observation_Labs"], input_dir, output_dir)

    
def merge_fhir_condition(input_dir, output_dir):
    merge_fhir_resource("Condition", [], input_dir, output_dir)

    
def merge_fhir_medication_request(input_dir, output_dir):
    merge_fhir_resource("MedicationRequest", [], input_dir, output_dir)

    
def merge_fhir_encounter(input_dir, output_dir):
    merge_fhir_resource("Encounter", [], input_dir, output_dir)


def merge_fhir(input_dir, output_dir):
    merge_fhir_patient(input_dir, output_dir)
    merge_fhir_lab(input_dir, output_dir)
    merge_fhir_condition(input_dir, output_dir)
    merge_fhir_medication_request(input_dir, output_dir)
    merge_fhir_encounter(input_dir, output_dir)

    
if __name__ == "__main__":
    input_dir, output_dir = sys.argv[1:]
    merge_fhir(input_dir, output_dir)