import locale
import math
import os
import hashlib
from datetime import datetime
from fpdf import FPDF

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.forms import formset_factory
from django.http import HttpResponse
from django.shortcuts import render, reverse, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import get_language

from user_profile.models import User, Participant
from certificate.forms import ActivityLinkForm
from certificate.models import ActivityLink, Certificate


#######################################
# PAGES
#######################################
@login_required()
def certificate(request):
    user = request.user

    if user.first_name and user.last_name and user.email:
        is_student = Participant.objects.filter(username=user.username).exists()
        ActivitiesForm = formset_factory(ActivityLinkForm, max_num=5, validate_max=True, can_delete=False)
        if request.method == "POST":
            formset = ActivitiesForm(request.POST)
            if formset.is_valid():
                for form in formset:
                    form_id = int(form.prefix.replace("form-", "")) + 2  # Activities start in the 2nd module
                    try:
                        activity_link = ActivityLink.objects.get(module_id=form_id, user=user)
                        activity_link.link = form.cleaned_data["link"]
                        activity_link.save()
                    except ActivityLink.DoesNotExist:
                        ActivityLink.objects.create(module_id=form_id,
                                                    user=user,
                                                    link=form.cleaned_data["link"])
                send_email_to_coordinator(user)
                user.requested_certificate = True
                user.date_of_request = datetime.now(tz=timezone.utc)
                user.save()
        else:
            if ActivityLink.objects.filter(user=user).exists():
                formset = ActivitiesForm(initial=[
                    {'link': ActivityLink.objects.get(module_id=2, user=user).link},
                    {'link': ActivityLink.objects.get(module_id=3, user=user).link},
                    {'link': ActivityLink.objects.get(module_id=4, user=user).link},
                    {'link': ActivityLink.objects.get(module_id=5, user=user).link},
                    {'link': ActivityLink.objects.get(module_id=6, user=user).link},
                ])
            else:
                formset = ActivitiesForm(initial=[{'link': ""}, {'link': ""}, {'link': ""}, {'link': ""}, {'link': ""}])

        user_requested_certificate = user.requested_certificate
        date_of_request = user.date_of_request or None
        context = {"user_requested_certificate": user_requested_certificate,
                   "date_of_request": date_of_request,
                   "user_is_registered": is_student,
                   "formset": formset}
        return render(request, 'user_profile/certificate.html', context)
    else:
        return redirect(reverse("profile"))


@login_required()
def change_links(request, next_url):
    """
    Page to the user reset the solicitation of certificate and be able to provide new links to their activities
    """
    user = request.user
    user.requested_certificate = False
    user.date_of_request = None
    user.save()

    return redirect(reverse(next_url))


@login_required()
def manage_certificates(request):
    """
    Loads page for organizers to manage and send certificates or messages
    """
    users = User.objects.filter(requested_certificate=True)
    for user in users:
        # TODO: improve performance if this becomes slow
        user.enrolled_at = None
        participant = Participant.objects.filter(username=user.username).first()
        if participant:
            user.enrolled_at = participant.enrolled_at

    if request.method == "POST":
        form = request.POST
        username = form.get("username")
        problems = form.getlist('checkboxes[]')
        send_email_to_user(problems, username)

    context = {"users": users}
    return render(request, 'certificate/manage_certificates.html', context)


def validate(request):
    if request.method == "POST":
        hash_to_check = request.POST.get("hash") or ""
        certificate_obj = Certificate.objects.get(certificate_hash=hash_to_check)

        if certificate_obj.certificate_type == "enrollment":
            certificate_file = generate_enrollment_letter(certificate_obj.user_id)
            response = HttpResponse(certificate_file, content_type='application/pdf')
            response["Content-Disposition"] = str(_('attachment; filename=WikiConecta - Enrollment - {name}.pdf').format(name=certificate_obj.user.username))
        else:
            certificate_file = generate_certificate(certificate_obj.user_id)
            response = HttpResponse(certificate_file, content_type='application/pdf')
            response["Content-Disposition"] = str(_('attachment; filename=WikiConecta - Certificate of Completion - {name}.pdf').format(name=certificate_obj.user.username))
        return response
    else:
        return render(request, 'certificate/validator.html')


#######################################
# PDF generators
#######################################
class SubsPDF(FPDF):
    def __init__(self, user_hash, orientation='P', unit='mm', format='A4'):
        super().__init__(orientation, unit, format)
        self.user_hash = user_hash

    def footer(self):
        self.set_y(-18)
        self.set_font('Times', '', 9)
        self.multi_cell(w=0, h=4.5, border=0, align='C', txt=str(_('The validity of this document can be checked at https://wikiconecta.toolforge.org/\nThe validation code is: {code}').format(code=self.user_hash)))


class CertificationPDF(FPDF):
    def __init__(self, user_hash, orientation='P', unit='mm', format='A4'):
        super().__init__(orientation, unit, format)
        self.user_hash = user_hash

    def footer(self):
        self.set_y(-25)
        self.set_font('Baloo2-Regular', '', 9)
        self.multi_cell(w=0, h=4.5, border=0, align='C', txt=str(_('The validity of this document can be checked at https://wikiconecta.toolforge.org/\nThe validation code is: {code}').format(code=self.user_hash)))


@login_required()
def enrollment_letter(request):
    user_id = request.user.id
    return generate_enrollment_letter(user_id)


def set_language_if_ptbr(language):
    if language == "pt-br":
        return "pt_BR"
    else:
        return language


def generate_enrollment_letter(user_id=None):
    user = User.objects.get(pk=user_id)

    certificate_obj, created = Certificate.objects.get_or_create(user_id=user.id, certificate_type="enrollment")
    if created:
        user_hash = hashlib.sha1(bytes("Enrollment letter " + certificate_obj.user.username + str(certificate_obj.date_issued), 'utf-8')).hexdigest()
        certificate_obj.certificate_hash = user_hash
        certificate_obj.save()
    else:
        user_hash = certificate_obj.certificate_hash

    # Create page
    pdf = SubsPDF(orientation='P', unit='mm', format='A4', user_hash=user_hash)

    pdf.set_margins(30, 10, 20)
    pdf.add_page()

    pdf.set_draw_color(75, 82, 209)
    pdf.set_text_color(255, 255, 255)  # Title in white color
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/wikiconecta-header.png'), x=0, y=0, w=210)
    # Box for the title
    pdf.set_text_color(255, 255, 255)  # Title in white color
    pdf.add_font(family='Baloo2-Bold', fname=os.path.join(settings.STATIC_ROOT, 'fonts/Baloo2-Bold.ttf'), uni=True)
    pdf.set_font('Baloo2-Bold', '', 25)  # Title in Times New Roman, bold, 15pt
    # Title text
    pdf.set_x(30)
    pdf.cell(w=150, h=12, border=0, ln=1, align='C', fill=False, txt='WikiConecta')
    pdf.set_text_color(0, 0, 0)  # Title in white color

    #######################################################################################################
    # Data
    #######################################################################################################
    pdf.set_xy(30, 42)  # Start the letter text at the 10x42mm point

    pdf.set_font('Times', '', 13)  # Text of the body in Times New Roman, regular, 13 pt

    locale.setlocale(locale.LC_TIME, set_language_if_ptbr(get_language()))
    pdf.cell(w=150, h=9, border=0, ln=1, align='L', txt=str(_('São Paulo, ')) + datetime.now().strftime(str(_("%B %d, %Y"))))

    pdf.cell(w=0, h=9, ln=1)  # New line

    #######################################################################################################
    # To whom it may concern
    #######################################################################################################
    pdf.set_font('Times', 'B', 13)  # Text of the addressing in Times New Roman, bold, 13 pt
    pdf.cell(w=150, h=9, txt=str(_('To whom it may concern')), border=0, ln=1, align='L')

    pdf.cell(w=0, h=9, ln=1)  # New line

    #######################################################################################################
    # User data
    #######################################################################################################
    try:
        name = user.first_name + ' ' + user.last_name
    except AttributeError:
        name = user.username  # User full name

    #######################################################################################################
    # Text
    #######################################################################################################
    pdf.set_font('Times', '', 13)  # Text of the addressing in Times New Roman, bold, 13 pt
    pdf.multi_cell(w=0, h=9, border=0, align='J', txt=str(_("""The WikiConecta course (https://w.wiki/7KwX) was developed by Wikimedia Brasil, a non-profit organization that works towards free knowledge under CNPJ 29.801.908/0001-86. To complete it, 20 hours of dedication are required, asynchronously and independently, and the course is available on an open online education platform, the Wikiversity.\n\nThe units were developed by Professor Amanda Chevtchouk Jurno, PhD, former Education and Scientific Dissemination Manager at Wikimedia Brasil, with scientific guidance from Professor João Alexandre Peschanski, PhD, Executive Director of Wikimedia Brasil. In order to adapt the content to the expectations of the Wikimedia Movement and ensure that it covered the information necessary to introduce educators to this universe, the course had strategic guidance from senior members of the Movement working in the area of Education in various regions of the world.\n\nThe objective of the course is to present in a condensed form the information that educators need to start using the Wikimedia projects with their students, especially in university extension. At the end of the course, participants should be able to basic edit four Wikimedia projects - Wikipedia, Wikidata, Wikimedia Commons and Wikiversity - and develop their own wiki-education programs, aiming to comply with Brazilian university extension guidelines.""")),)

    pdf.add_page()
    pdf.set_xy(30, 30)
    pdf.cell(w=0, h=9, txt=str(_('The WikiConecta is divided into six modules, each one divided into several units:')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.cell(w=0, h=9, txt=str(_('Module 1: Introduction')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Free Knowledge: potentials and advantages of using the Wikimedia Projects')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('The Wikimedia Projects and Organizations')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Collective ntelligence and open data')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Wikimedia programs, initiatives and events')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.cell(w=0, h=9, txt=str(_('Module 2: Wikipedia')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Wikipedia as an educational resource')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Diffusion and scientific dissemination on Wikipedia')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Content and equity gaps')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Principles and foundations of Wikipedia')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=8, txt=str(_('Entries and arrangement of information')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Editing Wikipedia')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Using Wikipedia with students')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.cell(w=0, h=9, txt=str(_('Module 3: Wikidata')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('The Wikimedia structured database')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Getting data: Wikidata Query Service and Scholia')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Entering data: how to use Zotero')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Bias and subjectivity in data')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Editing Wikidata')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Using Wikidata with students')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.cell(w=0, h=9, txt=str(_('Module 4: Wikimedia Commons')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Wikimedia audiovisual repository')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Creative commons and free licenses')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('How to use Wikimedia Commons')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('File upload')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Using Wikimedia Commons with students')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.add_page()
    pdf.set_xy(30, 30)
    pdf.cell(w=0, h=9, txt=str(_('Module 5: Wikiversity')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Wikiversity - the free university')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Open Educational Resources (OER) and Massive Open Online Courses (MOOCs)')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Editing the Wikiversity')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Using Wikiversity with students')), border=0, ln=1, align='L')
    pdf.set_font('Times', 'B', 13)
    pdf.cell(w=0, h=9, txt=str(_('Module 6: Education programs')), border=0, ln=1, align='L')
    pdf.set_font('Times', '', 13)
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Creating an education program with Wiki')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Program monitoring: Dashboard')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Potential financiers')), border=0, ln=1, align='L')
    pdf.set_x(42.5)
    pdf.cell(w=50, h=9, txt=str(_('Wikimedia and Education in Brazil')), border=0, ln=1, align='L')
    pdf.cell(w=0, h=9, ln=1)  # New line
    pdf.multi_cell(w=0, h=9, border=0, align='J', txt=str(_("""The course is free and the control of activities is carried out by resources on Wikimedia. This letter certifies that {name} is able to participate in the WikiConecta course. If requested, we can issue a declaration of course completion, once the participant has completed the proposed readings and activities.\n\nPlease do not hesitate to contact us to receive further information regarding the course.\n\nYours sincerely,""").format(name=user.first_name + " " + user.last_name)),)
    pdf.set_font('Times', '', 13)  # Text of the body in Times New Roman, regular, 13 pt

    #######################################################################################################
    # Footer
    #######################################################################################################
    pdf.cell(w=0, h=13, ln=1)  # Give some space for the signatures
    # Alexander Maximilian Hilsenbeck Filho's signature
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/alex.png'), x=56, y=223, w=28, h=16)
    pdf.set_y(230)
    pdf.multi_cell(w=80, h=9, border=0, align='C', txt=str(_("_________________________________\nALEXANDER MAXIMILIAN HILSENBECK FILHO\nCoordinator\nWikiConecta")),)
    pdf.set_xy(110, 230)
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/jap.png'), x=140.5, y=227, w=18, h=16)
    pdf.multi_cell(w=80, h=9, border=0, align='C', txt=str(_("_________________________________\nJOÃO ALEXANDRE PESCHANSKI\nExecutive Director\nWikimedia Brasil")),)

    # Generate the file
    file = pdf.output(dest='S').encode('latin-1')

    response = HttpResponse(file, content_type='application/pdf')
    response["Content-Disposition"] = str(_('attachment; filename=WikiConecta - Enrollment - {name}.pdf').format(name=user.username))
    return response


def generate_certificate(user_id=None):
    """
    Generates a certificate of completion for the course WikiConecta
    """
    user = User.objects.get(pk=user_id)
    certificate_obj, created = Certificate.objects.get_or_create(user_id=user.id, certificate_type="certificate")
    if created:
        user_hash = hashlib.sha1(bytes("Enrollment letter " + certificate_obj.user.username + str(certificate_obj.date_issued), 'utf-8')).hexdigest()
        certificate_obj.certificate_hash = user_hash
        certificate_obj.save()
    else:
        user_hash = certificate_obj.certificate_hash

    # Create the page
    pdf = CertificationPDF(orientation='L', unit='mm', format='A4', user_hash=user_hash)
    pdf.add_page()
    pdf.set_text_color(74, 81, 210)  # purple

    #######################################################################################################
    # Header
    #######################################################################################################
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/wikiconecta-background.png'), x=0, y=0, w=297, h=210)
    pdf.set_y(20)  # Start the letter text at the 10x42mm point

    pdf.add_font(family='Baloo2-Regular', fname=os.path.join(settings.STATIC_ROOT, 'fonts/Baloo2-Regular.ttf'), uni=True)
    pdf.add_font(family='Baloo2-Bold', fname=os.path.join(settings.STATIC_ROOT, 'fonts/Baloo2-Bold.ttf'), uni=True)
    pdf.set_font(family='Baloo2-Regular', size=37)  # Text of the body in Times New Roman, regular, 13 pt

    locale.setlocale(locale.LC_TIME, set_language_if_ptbr(get_language()))  # Setting the language to portuguese for the date
    pdf.cell(w=0, h=10, border=0, ln=1, align='C', txt=str(_('CERTIFICATE')))

    pdf.set_y(36)
    pdf.set_font('Baloo2-Regular', '', size=15)
    pdf.cell(w=0, h=10, border=0, ln=1, align='C', txt=str(_('We grant this certificate to')))

    #######################################################################################################
    # User name
    #######################################################################################################
    name = user.first_name + " " + user.last_name  # User full name
    pdf.set_font('Baloo2-Regular', '', 35)
    name_size = pdf.get_string_width(name)

    if name_size > 287:
        # Try to eliminate the prepositions
        name_split = [name_part for name_part in name.split(' ') if not name_part.islower()]
        # There's a first and last names and at least one middle name
        if len(name_split) > 2:
            first_name = name_split[0]
            last_name = name_split[-1]
            middle_names = [md_name[0] + '.' for md_name in name_split[1:-1]]
            name = first_name + ' ' + ' '.join(middle_names) + ' ' + last_name
            name_size = pdf.get_string_width(name)

        # Even abbreviating, there is still the possibility that the name is too big, so
        # we need to adjust it to the proper size
        if name_size > 287:
            pdf.set_font('Baloo2-Regular', '', math.floor(287 * 35 / name_size))

    pdf.set_y(55)
    pdf.cell(w=0, h=10, border=0, ln=1, align='C', txt=name)
    pdf.cell(w=0, h=10, ln=1)  # New line

    #######################################################################################################
    # for having completed the course
    #######################################################################################################
    pdf.set_font('Baloo2-Regular', '', 15)
    pdf.set_y(70)
    pdf.cell(w=0, h=10, border=0, ln=1, align='C', txt=str(_('for completing the readings and tasks of the online course')))

    #######################################################################################################
    # Initiative
    #######################################################################################################
    pdf.set_xy(164, 86)
    pdf.set_font('Baloo2-Bold', '', 11)
    pdf.cell(w=20, h=10, border=0, ln=0, align='L', txt=str(_('Initiative:')))

    #######################################################################################################
    # Footer
    #######################################################################################################
    # Alexander Maximilian Hilsenbeck Filho's signature
    pdf.set_xy(80, 131)
    pdf.multi_cell(w=80, h=5, border=0, align='C',
                   txt=str(
                       _("_______________________________________\nALEXANDER MAXIMILIAN HILSENBECK FILHO\nCoordinator\nWikiConecta")
                   ), )
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/alex.png'), x=106, y=122, w=28, h=16)
    # João Alexandre Peschanski's signature
    pdf.set_xy(155, 131)
    pdf.multi_cell(w=80, h=5, border=0, align='C', txt=str(
        _("_________________________________\nJOÃO ALEXANDRE PESCHANSKI\nExecutive Director\nWikimedia Brasil")), )
    pdf.image(os.path.join(settings.STATIC_ROOT, 'images/jap.png'), x=186, y=125, w=18, h=16)

    # Text
    pdf.set_xy(50, 166)
    pdf.multi_cell(w=197, h=5, border=0, align='C', txt=str(_('''The WikiConecta course does not have record control, readings and tasks are freely accessible.\nThis certificate is therefore not recognized as an official diploma. The course totals twenty hours.''')))

    # Generate the file
    file = pdf.output(dest='S').encode('latin-1')
    # response = HttpResponse(file, content_type='application/pdf')
    # response["Content-Disposition"] = str(_('attachment; filename=WikiConecta - Certificate of Completion - {name}.pdf').format(name=user.username))
    # return response
    return file


#######################################
# FUNCTIONS
#######################################
def build_message(problems, user):
    """
    Builds the message to the participants that solicited a certificate with the decision of the organizers
    :param problems: The list of modules in which the participant has problems in their activities
    :param user: participant being analyzed
    :return: the message to send to the user via email
    """
    message_parts = {
        "greetings": str(_("""Dear {name},<br><br>We hope this message finds you well. We want to inform you about the status of your participation in the <b>WikiConecta</b> online course.""").format(name=user.first_name)),
        "with_problems": str(_("""After a thorough review of your assessments of the course, we noticed that you've encountered some challenges with specific assignments or assessments. While your overall engagement and commitment to the course have been commendable, we want to ensure that you have a full grasp of the subjects of the course.<br>We appreciate your dedication to the course and your willingness to engage with the material. We want to emphasize that our goal is to support your learning journey. To that end, we invite you to work on the assignments and resubmit them for reevaluation.<br><br>The list of modules in which we identified problems in the assignments is available below. If you have any questions or concerns about them or about our evaluation, please feel free to contact the organizers of the course.<br><br><b>List of modules</b>""")),
        "without_problems": str(_("""In name of <b><i>Wikimedia Brasil</i></b>, we are excited to inform you that your <b>Certificate of Completion</b> for the WikiConecta online course is now ready for download and verification.<br><br>You successfully completed all the required assignments, demonstrating your dedication and commitment to learning. You certificate serves as a testament to your accomplishment in WikiConecta and your commitment to contribute in building the Free Knowledge ecosystem<br><br>Attached you will find your certificate.""")),
        "signature": str(_("""<font style='color:#4A51D2; font-weight:bold; font-style:italic;'>WikiConecta: Wikipedia in all its extension</font><br><a target='_blank' href='https://pt.wikiversity.org/wiki/WikiConecta'>https://pt.wikiversity.org/wiki/WikiConecta</a>""")),
        "1": str(_("""<li><i>Module 1 - Introduction</i></li>""")),
        "2": str(_("""<li><i>Module 2 - Wikipedia</i></li>""")),
        "3": str(_("""<li><i>Module 3 - Wikidata</i></li>""")),
        "4": str(_("""<li><i>Module 4 - Wikimedia Commons</i></li>""")),
        "5": str(_("""<li><i>Module 5 - Wikiversity</i></li>""")),
        "6": str(_("""<li><i>Module 6 - Education programs</i></li>"""))
    }

    message = message_parts["greetings"]
    if not problems:
        message += "<br><br>" + message_parts["without_problems"]
    else:
        message += "<br><br>" + message_parts["with_problems"] + "<br><ul>"
        for problem in problems:
            message += message_parts[problem]
        message += "<br></ul>"

    message += "<br><br>" + message_parts["signature"]

    return message


def send_email_to_user(problems, username):
    """
    Sends an email with a message to the participant that requested a certificate
    :param list problems: The list of modules in which the participant has problems in their activities
    :param User user: participant being analyzed
    :redirect: Manage other certificates
    """
    user_obj = User.objects.get(username=username)
    from_email = settings.EMAIL_HOST_USER
    to = [user_obj.email]
    bcc = settings.COORDINATORS_EMAILS
    body = build_message(problems, user_obj)
    subject = str(_("WikiConecta - Certificate of Completion request"))

    message = EmailMultiAlternatives(subject=subject,
                                     body="",
                                     from_email=from_email,
                                     to=to,
                                     bcc=bcc)

    message.attach_alternative(body, "text/html")
    if not problems:
        message.attach(filename=str(_("WikiConecta - Certificate of Completion - {name}.pdf")).format(name=username),
                       content=generate_certificate(user_obj.id),
                       mimetype='application/pdf')

    message.send()

    return redirect(reverse("manage_certificates"))


def build_message_for_coordinator(user):
    """
    Builds the message to the coordinators relaying that a participant solicited a certificate to the coordinators
    :param user: participant that solicited the certificate
    :return: the message to send to the coordinator via email
    """
    message_parts = {
        "greetings": str(_("""Dear coordinators,<br><br>The participant <b>{name} (User:{username})</b> of the <b>WikiConecta</b> online course has requested that you review their activities and, if correct, send them a certificate of conclusion of the course.""").format(name=user.first_name + " " + user.last_name, username=user.username)),
        "instructions": str(_("""You can find this and other participants with pending evaluations at https://wikiconecta.toolforge.org/manage_certificates.""")),
        "signature": str(_("""<font style='color:#4A51D2; font-weight:bold; font-style:italic;'>WikiConecta: Wikipedia in all its extension</font><br><a target='_blank' href='https://pt.wikiversity.org/wiki/WikiConecta'>https://pt.wikiversity.org/wiki/WikiConecta</a>"""))
    }

    message = message_parts["greetings"] + "<br><br>" + message_parts["instructions"] + "<br><br>" + message_parts["signature"]

    return message


def send_email_to_coordinator(user):
    """
    Sends an email with a message to the coordinator informing that the participant requested a certificate
    :param User user: participant requesting certificate
    """
    from_email = settings.EMAIL_HOST_USER
    to = settings.COORDINATORS_EMAILS
    body = build_message_for_coordinator(user)
    subject = str(_("WikiConecta - Certificate of Completion request"))

    message = EmailMultiAlternatives(subject=subject,
                                     body="",
                                     from_email=from_email,
                                     to=to)

    message.attach_alternative(body, "text/html")
    message.send()
