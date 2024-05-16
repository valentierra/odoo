# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import date, timedelta

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged("post_install", "-at_install")
class TestUi(TestPointOfSaleHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Disable any programs during the test
        cls.env['loyalty.program'].search([]).write({'active': False})
        cls.promo_programs = cls.env["loyalty.program"]

        # code promo program -> discount on specific products
        cls.code_promo_program = cls.env['loyalty.program'].create({
            'name': 'Promo Code Program - Discount on Specific Products',
            'program_type': 'promotion',
            'trigger': 'with_code',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'mode': 'with_code',
                'code': 'promocode',
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 50,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_ids': cls.whiteboard_pen | cls.magnetic_board | cls.desk_organizer,
            })],
        })
        cls.promo_programs |= cls.code_promo_program

        # auto promo program on current order
        #   -> discount on cheapest product
        cls.auto_promo_program_current = cls.env['loyalty.program'].create({
            'name': 'Auto Promo Program - Cheapest Product',
            'program_type': 'promotion',
            'trigger': 'auto',
            'rule_ids': [(0, 0, {})],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 90,
                'discount_mode': 'percent',
                'discount_applicability': 'cheapest',
            })]
        })
        cls.promo_programs |= cls.auto_promo_program_current

        # auto promo program on next order
        #   -> discount on order (global discount)
        cls.auto_promo_program_next = cls.env['loyalty.program'].create({
            'name': 'Auto Promo Program - Global Discount',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'future',
            'rule_ids': [(0, 0, {})],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })]
        })
        cls.promo_programs |= cls.auto_promo_program_next
        cls.promo_programs.write({
            'pos_config_ids': [Command.link(cls.main_pos_config.id)],
        })

        # coupon program -> free product
        cls.coupon_program = cls.env['loyalty.program'].create({
            'name': 'Coupon Program - Buy 3 Take 2 Free Product',
            'program_type': 'coupons',
            'trigger': 'with_code',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_ids': cls.desk_organizer,
                'reward_point_mode': 'unit',
                'minimum_qty': 3,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': cls.desk_organizer.id,
                'reward_product_qty': 1,
                'required_points': 1.5,
            })],
            'pos_config_ids': [Command.link(cls.main_pos_config.id)],
        })

        # Create coupons for the coupon program and change the code
        # to be able to use them in the frontend tour.
        cls.env["loyalty.generate.wizard"].with_context(
            {"active_id": cls.coupon_program.id}
        ).create({"coupon_qty": 4, 'points_granted': 4.5}).generate_coupons()
        cls.coupon1, cls.coupon2, cls.coupon3, cls.coupon4 = cls.coupon_program.coupon_ids
        cls.coupon1.write({"code": "1234"})
        cls.coupon2.write({"code": "5678"})
        cls.coupon3.write({"code": "1357"})
        cls.coupon4.write({"code": "2468"})

    def setUp(self):
        super().setUp()
        # Set the programs to the pos config.
        # Remove fiscal position and pricelist.
        self.main_pos_config.write({
            'tax_regime_selection': False,
            'use_pricelist': False,
        })
        self.main_pos_config.with_user(self.pos_user).open_ui()

    def create_programs(self, details):
        """
        Create loyalty programs based on the details given.
        :param details: list of tuple ('name': str, 'program_type': 'gift_card' or 'ewallet')
        """
        LoyaltyProgram = self.env['loyalty.program']
        programs = {} # map: name -> program
        for (name, program_type) in details:
            program_id = LoyaltyProgram.create_from_template(program_type)['res_id']
            program = LoyaltyProgram.browse(program_id)
            program.write({'name': name})
            programs[name] = program
        return programs

    def test_pos_loyalty_tour_basic(self):
        """PoS Loyalty Basic Tour"""
        ##
        # Tour Part 1
        # This part will generate coupons for `auto_promo_program_next`
        # that will be used in the second part of the tour.
        #
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        self.start_pos_tour("PosLoyaltyTour1")

        # check coupon usage
        self.assertEqual(self.coupon1.points, 0, 'The coupon should have consumed its points.')
        self.assertEqual(self.coupon2.points, 4.5, 'The coupon was used but never validated.')
        # check pos_order_count in each program
        self.assertEqual(self.auto_promo_program_current.pos_order_count, 3)
        self.assertEqual(self.auto_promo_program_next.pos_order_count, 0)
        self.assertEqual(self.code_promo_program.pos_order_count, 1)
        self.assertEqual(self.coupon_program.pos_order_count, 1)
        # check number of generated coupons
        self.assertEqual(len(self.auto_promo_program_next.coupon_ids), 5)
        # check number of orders in the session
        pos_session = self.main_pos_config.current_session_id
        self.assertEqual(
            len(pos_session.order_ids), 5, msg="5 orders were made in tour part1."
        )

        ##
        # Tour Part 2
        # The coupons generated in the first part will be used in this tour.
        #

        # Manually set the code for some `auto_promo_program_next` coupons
        # to be able to use them in defining the part2 tour.
        (
            promo_coupon1,
            promo_coupon2,
            promo_coupon3,
            promo_coupon4,
            *_,
        ) = self.auto_promo_program_next.coupon_ids
        promo_coupon1.write({"code": "123456"})
        promo_coupon2.write({"code": "345678"})
        promo_coupon3.write({"code": "567890"})
        promo_coupon4.write({"code": "098765"})

        self.coupon2.points = 6
        self.coupon3.points = 3

        # use here the generated coupon
        self.start_pos_tour("PosLoyaltyTour2")
        # check pos_order_count in each program
        self.assertEqual(self.auto_promo_program_current.pos_order_count, 6)
        self.assertEqual(self.auto_promo_program_next.pos_order_count, 2)
        self.assertEqual(self.code_promo_program.pos_order_count, 2)
        self.assertEqual(self.coupon_program.pos_order_count, 3)

    def test_loyalty_validity_dates_and_use(self):
        # Tests date validity and max usage for an automatic program.
        self.auto_promo_program_current.write({
            'date_to': date.today() - timedelta(days=2),
            'limit_usage': True,
            'max_usage': 1,
        })

        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })

        # First tour check that the promotion is not applied
        self.start_pos_tour("PosLoyaltyValidity1")

        self.auto_promo_program_current.write({
            'date_to': date.today() + timedelta(days=2),
        })

        # Second tour that does 2 orders, the first should have the rewards, the second should not
        self.start_pos_tour("PosLoyaltyValidity2")

    def test_loyalty_free_product_rewards(self):
        free_product = self.env['loyalty.program'].create({
            'name': 'Buy 2 Take 1 desk_organizer',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_ids': self.desk_organizer,
                'reward_point_mode': 'unit',
                'minimum_qty': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.desk_organizer.id,
                'reward_product_qty': 1,
                'required_points': 2,
            })],
        })
        free_other_product = self.env['loyalty.program'].create({
            'name': 'Buy 3 magnetic_board, Take 1 whiteboard_pen',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_ids': self.magnetic_board,
                'reward_point_mode': 'unit',
                'minimum_qty': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.whiteboard_pen.id,
                'reward_product_qty': 1,
                'required_points': 3,
            })],
        })
        free_multi_product = self.env['loyalty.program'].create({
            'name': '2 items of shelves, get desk_pad/monitor_stand free',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_ids': (self.wall_shelf | self.small_shelf).ids,
                'reward_point_mode': 'unit',
                'minimum_qty': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_tag_id': self.env['product.tag'].create({
                    'name': 'reward_product_tag',
                    'product_product_ids': (self.desk_pad | self.monitor_stand).ids,
                }).id,
                'reward_product_qty': 1,
                'required_points': 2,
            })],
        })

        (self.promo_programs | self.coupon_program).write({'active': False})

        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        self.start_pos_tour("PosLoyaltyFreeProductTour")

        # Keep the tour to generate 4 orders for the free_product and free_other_product programs.
        # 2 of them don't use a program.
        # 1 uses free_product.
        # 1 uses free_other_product.
        # This is to take into account the fact that during tours, we can't test the "non-occurence" of something.
        # It would be nice to have a check like: Validate that a reward is "not" there.
        self.assertEqual(free_product.pos_order_count, 1)
        self.assertEqual(free_other_product.pos_order_count, 2)

        # There is the 5th order that tests multi_product reward.
        # It attempted to add one reward product, removed it, then add the second.
        # The second reward was synced with the order.
        self.assertEqual(free_multi_product.pos_order_count, 1)

    def test_loyalty_free_product_loyalty_program(self):
        # In this program, each whiteboard pen gives 1 point.
        # 4 points can be used to get a free whiteboard pen.
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })

        loyalty_program = self.env['loyalty.program'].create({
            'name': 'Buy 4 whiteboard_pen, Take 1 whiteboard_pen',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'rule_ids': [(0, 0, {
                'product_ids': self.whiteboard_pen.ids,
                'reward_point_mode': 'unit',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.whiteboard_pen.id,
                'reward_product_qty': 1,
                'required_points': 4,
            })],
        })

        (self.promo_programs | self.coupon_program).write({'active': False})

        partner_aaa = self.env['res.partner'].create({'name': 'AAA Test Partner'})
        partner_bbb = self.env['res.partner'].create({'name': 'BBB Test Partner'})
        partner_ccc = self.env['res.partner'].create({'name': 'CCC Test Partner'})

        # Part 1
        self.start_pos_tour("PosLoyaltyLoyaltyProgram1")

        aaa_loyalty_card = loyalty_program.coupon_ids.filtered(lambda coupon: coupon.partner_id.id == partner_aaa.id)

        self.assertEqual(loyalty_program.pos_order_count, 1)
        self.assertAlmostEqual(aaa_loyalty_card.points, 4)

        # Part 2
        self.start_pos_tour("PosLoyaltyLoyaltyProgram2")

        self.assertEqual(loyalty_program.pos_order_count, 2, msg='Only 2 orders should have reward lines.')
        self.assertAlmostEqual(aaa_loyalty_card.points, 1)

        bbb_loyalty_card = loyalty_program.coupon_ids.filtered(lambda coupon: coupon.partner_id.id == partner_bbb.id)
        ccc_loyalty_card = loyalty_program.coupon_ids.filtered(lambda coupon: coupon.partner_id.id == partner_ccc.id)

        self.assertAlmostEqual(bbb_loyalty_card.points, 3, msg='Reference: Order3_BBB')
        self.assertAlmostEqual(ccc_loyalty_card.points, 4, msg='Reference: Order2_CCC')

        reward_orderline = self.main_pos_config.current_session_id.order_ids[-1].lines.filtered(lambda line: line.is_reward_line)
        self.assertEqual(len(reward_orderline.ids), 0, msg='Reference: Order4_no_reward. Last order should have no reward line.')

    def test_loyalty_free_product_zero_sale_price_loyalty_program(self):
        # In this program, each $ spent gives 1 point.
        # 5 points can be used to get a free whiteboard pen.
        # and the whiteboard pen sale price is zero
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        self.whiteboard_pen.write({'lst_price': 1})

        loyalty_program = self.env['loyalty.program'].create({
            'name': 'Loyalty Program',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'rule_ids': [(0, 0, {
                'reward_point_amount': 1,
                'reward_point_mode': 'money',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.whiteboard_pen.id,
                'reward_product_qty': 1,
                'required_points': 5,
            })],
        })

        (self.promo_programs | self.coupon_program).write({'active': False})

        partner_aaa = self.env['res.partner'].create({'name': 'AAA Test Partner'})

        self.start_pos_tour("PosLoyaltyLoyaltyProgram3")

        aaa_loyalty_card = loyalty_program.coupon_ids.filtered(lambda coupon: coupon.partner_id.id == partner_aaa.id)

        self.assertEqual(loyalty_program.pos_order_count, 1)
        self.assertAlmostEqual(aaa_loyalty_card.points, 5.2)

    def test_pos_loyalty_tour_max_amount(self):
        """Test the loyalty program with a maximum amount and product with different taxe."""

        self.env['loyalty.program'].search([]).write({'active': False})

        self.promo_product = self.env["product.product"].create(
            {
                "name": "Promo Product",
                "type": "service",
                "list_price": 30,
                "available_in_pos": True,
            }
        )
        tax01 = self.env["account.tax"].create({
            "name": "C01 Tax",
            "amount": "0.00",
        })
        tax02 = self.env["account.tax"].create({
            "name": "C02 Tax",
            "amount": "0.00",
        })

        self.productA = self.env["product.product"].create(
            {
                "name": "Product A",
                "is_storable": True,
                "list_price": 15,
                "available_in_pos": True,
                "taxes_id": [(6, 0, [tax01.id])],
            }
        )

        # create another product with different taxes_id
        self.productB = self.env["product.product"].create(
            {
                "name": "Product B",
                "is_storable": True,
                "list_price": 25,
                "available_in_pos": True,
                "taxes_id": [(6, 0, [tax02.id])]
            }
        )

        self.env['loyalty.program'].create({
            'name': 'Promo Program - Max Amount',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_domain': '[["product_variant_ids.name","=","Promo Product"]]',
                'reward_point_mode': 'unit',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount_product_ids': (self.productA | self.productB).ids,
                'required_points': 1,
                'discount': 100,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_max_amount': 40,
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.start_pos_tour("PosLoyaltyTour3")

    def test_gift_card_program_create_set(self):
        """
        Test for gift card program when pos.config.gift_card_settings == 'create_set'.
        """
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the gift_card_product_50 because it's shared among new gift card programs.
        self.env.ref('loyalty.gift_card_product_50').write({'active': True})
        # Create gift card program
        gift_card_program = self.create_programs([('arbitrary_name', 'gift_card')])['arbitrary_name']
        # Change the gift card program settings
        self.main_pos_config.write({'gift_card_settings': 'create_set'})
        # Run the tour to create a gift card
        self.start_pos_tour("GiftCardProgramCreateSetTour1")
        # Check that gift cards are created
        self.assertEqual(len(gift_card_program.coupon_ids), 1)
        # Change the code to 044123456 so that we can use it in the next tour.
        # Make sure it starts with 044 because it's the prefix of the loyalty cards.
        gift_card_program.coupon_ids.code = '044123456'
        # Run the tour to use the gift card
        self.start_pos_tour("GiftCardProgramCreateSetTour2")
        # Check that gift cards are used
        self.assertEqual(gift_card_program.coupon_ids.points, 46.8)

    def test_gift_card_program_scan_use(self):
        """
        Test for gift card program with pos.config.gift_card_settings == 'scan_use'.
        - The gift card coupon codes are known before opening pos.
        - They will be scanned and paid by the customer which links the coupon to the order.
            - Meaning, it's paid.
        - Then it will be scanned for usage.
        """
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        # set the nomenclature to GS1
        barcodes_gs1_nomenclature = self.env.ref("barcodes_gs1_nomenclature.default_gs1_nomenclature")
        self.main_pos_config.company_id.write({
            'nomenclature_id': barcodes_gs1_nomenclature.id
        })

        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the gift_card_product_50 because it's shared among new gift card programs.
        self.env.ref('loyalty.gift_card_product_50').write({'active': True})
        # Create gift card program
        gift_card_program = self.create_programs([('arbitrary_name', 'gift_card')])['arbitrary_name']
        # Change the gift card program settings
        self.main_pos_config.write({'gift_card_settings': 'scan_use'})
        # Generate 5$ gift card.
        self.env["loyalty.generate.wizard"].with_context(
            {"active_id": gift_card_program.id}
        ).create({"coupon_qty": 1, 'points_granted': 5}).generate_coupons()
        # Change the code of the gift card.
        gift_card_program.coupon_ids.code = '043123456'
        # Run the tour. It will pay the gift card and use it.
        self.start_pos_tour("GiftCardProgramScanUseTour")
        # Check that gift cards are used
        self.assertAlmostEqual(gift_card_program.coupon_ids.points, 0, places=2)
        # 3 order should be created.
        self.assertEqual(len(self.main_pos_config.current_session_id.order_ids), 3)

    def test_ewallet_program(self):
        """
        Test for ewallet program.
        - Collect points in EWalletProgramTour1.
        - Use points in EWalletProgramTour2.
        """
        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the ewallet_product_50 because it's shared among new ewallet programs.
        self.env.ref('loyalty.ewallet_product_50').write({'active': True})
        # Create ewallet program
        ewallet_program = self.create_programs([('arbitrary_name', 'ewallet')])['arbitrary_name']
        # Create test partners
        partner_aaa = self.env['res.partner'].create({'name': 'AAAAAAA'})
        partner_bbb = self.env['res.partner'].create({'name': 'BBBBBBB'})

        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        # Run the tour to topup ewallets.
        self.start_pos_tour("EWalletProgramTour1")
        # Check that ewallets are created for partner_aaa.
        ewallet_aaa = self.env['loyalty.card'].search([('partner_id', '=', partner_aaa.id), ('program_id', '=', ewallet_program.id)])
        self.assertEqual(len(ewallet_aaa), 1)
        self.assertAlmostEqual(ewallet_aaa.points, 50, places=2)
        # Check that ewallets are created for partner_bbb.
        ewallet_bbb = self.env['loyalty.card'].search([('partner_id', '=', partner_bbb.id), ('program_id', '=', ewallet_program.id)])
        self.assertEqual(len(ewallet_bbb), 1)
        self.assertAlmostEqual(ewallet_bbb.points, 10, places=2)
        # Run the tour consume ewallets.
        self.start_pos_tour("EWalletProgramTour2")
        # Check that ewallets are consumed for partner_aaa.
        self.assertAlmostEqual(ewallet_aaa.points, 0, places=2)
        # Check final balance after consumption and refund eWallet for partner_bbb.
        self.assertAlmostEqual(ewallet_bbb.points, 20, places=2)

    def test_multiple_gift_wallet_programs(self):
        """
        Test for multiple gift_card and ewallet programs.
        """
        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the gift_card_product_50 and ewallet_product_50 because they're shared among new programs.
        self.env.ref('loyalty.gift_card_product_50').write({'active': True})
        self.env.ref('loyalty.ewallet_product_50').write({'active': True})
        # Create programs
        programs = self.create_programs([
            ('gift_card_1', 'gift_card'),
            ('gift_card_2', 'gift_card'),
            ('ewallet_1', 'ewallet'),
            ('ewallet_2', 'ewallet')
        ])
        # Change the gift card program settings
        self.main_pos_config.write({'gift_card_settings': 'create_set'})
        # Create test partners
        partner_aaa = self.env['res.partner'].create({'name': 'AAAAAAA'})
        partner_bbb = self.env['res.partner'].create({'name': 'BBBBBBB'})
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        # Run the tour to topup ewallets.
        self.start_pos_tour("MultipleGiftWalletProgramsTour")
        # Check the created gift cards.
        self.assertEqual(len(programs['gift_card_1'].coupon_ids), 1)
        self.assertAlmostEqual(programs['gift_card_1'].coupon_ids.points, 10)
        self.assertEqual(len(programs['gift_card_2'].coupon_ids), 1)
        self.assertAlmostEqual(programs['gift_card_2'].coupon_ids.points, 20)
        # Check the created ewallets.
        ewallet_1_aaa = self.env['loyalty.card'].search([('partner_id', '=', partner_aaa.id), ('program_id', '=', programs['ewallet_1'].id)])
        self.assertEqual(len(ewallet_1_aaa), 1)
        self.assertAlmostEqual(ewallet_1_aaa.points, 18, places=2)
        ewallet_2_aaa = self.env['loyalty.card'].search([('partner_id', '=', partner_aaa.id), ('program_id', '=', programs['ewallet_2'].id)])
        self.assertEqual(len(ewallet_2_aaa), 1)
        self.assertAlmostEqual(ewallet_2_aaa.points, 40, places=2)
        ewallet_1_bbb = self.env['loyalty.card'].search([('partner_id', '=', partner_bbb.id), ('program_id', '=', programs['ewallet_1'].id)])
        self.assertEqual(len(ewallet_1_bbb), 1)
        self.assertAlmostEqual(ewallet_1_bbb.points, 50, places=2)
        ewallet_2_bbb = self.env['loyalty.card'].search([('partner_id', '=', partner_bbb.id), ('program_id', '=', programs['ewallet_2'].id)])
        self.assertEqual(len(ewallet_2_bbb), 1)
        self.assertAlmostEqual(ewallet_2_bbb.points, 0, places=2)

    def test_coupon_change_pricelist(self):
        """Test coupon program with different pricelists."""

        product_1 = self.env["product.product"].create(
            {
                "name": "Test Product 1",
                "is_storable": True,
                "list_price": 25,
                "available_in_pos": True,
            }
        )

        tax01 = self.env["account.tax"].create({
            "name": "C01 Tax",
            "amount": "0.00",
        })

        product_2 = self.env["product.product"].create(
            {
                "name": "Test Product 2",
                "is_storable": True,
                "list_price": 25,
                "available_in_pos": True,
                "taxes_id": [(6, 0, [tax01.id])],
            }
        )

        pricelist = self.env["product.pricelist"].create({
            "name": "Test multi-currency",
            "discount_policy": "without_discount",
            "currency_id": self.env.ref("base.USD").id,
            "item_ids": [
                (0, 0, {
                    "base": "standard_price",
                    "product_id": product_1.id,
                    "compute_price": "percentage",
                    "percent_price": 50,
                }),
                (0, 0, {
                    "base": "standard_price",
                    "product_id": product_2.id,
                    "compute_price": "percentage",
                    "percent_price": 50,
                })
            ]
        })

        self.cash_journal = self.env['account.journal'].create(
            {'name': 'CASH journal', 'type': 'cash', 'code': 'CSH00'})
        self.cash_payment_method = self.env['pos.payment.method'].create({
            'name': 'Cash Test',
            'journal_id': self.cash_journal.id,
            'receivable_account_id': self.main_pos_config.payment_method_ids.filtered(lambda s: s.is_cash_count).receivable_account_id.id
        })

        self.main_pos_config2 = self.main_pos_config.copy({
            'payment_method_ids': self.cash_payment_method
        })

        loyalty_program = self.env['loyalty.program'].create({
            'name': 'Coupon Program - Pricelist',
            'program_type': 'coupons',
            'trigger': 'with_code',
            'applies_on': 'current',
            'pos_ok': True,
            'pos_config_ids': [Command.link(self.main_pos_config2.id)],
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                'minimum_amount': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 100,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })

        self.env["loyalty.generate.wizard"].with_context(
            {"active_id": loyalty_program.id}
        ).create({"coupon_qty": 1, 'points_granted': 4.5}).generate_coupons()
        self.coupon1 = loyalty_program.coupon_ids
        self.coupon1.write({"code": "abcda"})

        self.main_pos_config2.write({
            'use_pricelist': True,
            'available_pricelist_ids': [(4, pricelist.id), (4, self.main_pos_config.pricelist_id.id)],
            'pricelist_id': pricelist.id,
        })

        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })

        self.main_pos_config2.with_user(self.pos_user).open_ui()
        self.start_tour(
            "/pos/web?config_id=%d" % self.main_pos_config2.id,
            "PosLoyaltyTour4",
            login="pos_user",
        )

    def test_promotion_program_with_global_discount(self):
        """
        - Create a promotion with a discount of 10%
        - Create a product with no taxes
        - Enable the global discount feature, and make sure the Discount product
            has a tax set on it.
        """

        if not self.env["ir.module.module"].search([("name", "=", "pos_discount"), ("state", "=", "installed")]):
            self.skipTest("pos_discount module is required for this test")
        LoyaltyProgram = self.env['loyalty.program']
        (LoyaltyProgram.search([])).write({'pos_ok': False})

        tax = self.env["account.tax"].create({
            "name": "C01 Tax",
            "amount": "0.00",
        })

        self.discount_product = self.env["product.product"].create(
            {
                "name": "Discount Product",
                "type": "service",
                "list_price": 0,
                "available_in_pos": True,
                "taxes_id": [(6, 0, [tax.id])],
            }
        )

        self.cash_journal = self.env['account.journal'].create(
            {'name': 'CASH journal', 'type': 'cash', 'code': 'CSHDI'})
        self.cash_payment_method = self.env['pos.payment.method'].create({
            'name': 'Cash Test',
            'journal_id': self.cash_journal.id,
            'receivable_account_id': self.main_pos_config.payment_method_ids.filtered(
                lambda s: s.is_cash_count).receivable_account_id.id
        })

        self.main_pos_config2 = self.main_pos_config.copy({
            'payment_method_ids': self.cash_payment_method
        })
        self.main_pos_config2.write({
            'module_pos_discount' : True,
            'discount_product_id': self.discount_product.id,
            'discount_pc': 20,
        })

        self.loyalty_program = self.env['loyalty.program'].create({
            'name': 'Coupon Program - Pricelist',
            'program_type': 'coupons',
            'trigger': 'with_code',
            'applies_on': 'current',
            'pos_ok': True,
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                'minimum_amount': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })

        self.product = self.env["product.product"].create(
            {
                "name": "Test Product 1",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
            }
        )

        self.main_pos_config2.with_user(self.pos_user).open_ui()

        self.start_pos_tour("PosCouponTour5", pos_config=self.main_pos_config2)

    def test_loyalty_program_using_same_product(self):
        """
        - Create a loyalty program giving free product A for 30 points
        - Trigger the condition of the program using the same product A
        """
        LoyaltyProgram = self.env['loyalty.program']
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        self.product_a = self.env["product.product"].create({
            "name": "Test Product A",
            "is_storable": True,
            "list_price": 10,
            "available_in_pos": True,
        })

        self.loyalty_program = self.env['loyalty.program'].create({
            'name': 'Loyalty Program Test',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'pos_ok': True,
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 10,
                'minimum_amount': 5,
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'required_points': 30,
                'reward_product_id': self.product_a.id,
                'reward_product_qty': 1,
            })],
        })

        partner_aaa = self.env['res.partner'].create({'name': 'AAA Partner'})
        self.env['loyalty.card'].create({
            'partner_id': partner_aaa.id,
            'program_id': self.loyalty_program.id,
            'points': 30,
        })

        self.main_pos_config.open_ui()

        self.start_pos_tour("PosLoyaltyFreeProductTour2")

    def test_refund_with_gift_card(self):
        """When adding a gift card when there is a refund in the order, the amount
        of the gift card is set to the amount of the refund"""
        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the gift_card_product_50 because it's shared among new gift card programs.
        self.env.ref('loyalty.gift_card_product_50').write({'active': True})
        # Create gift card program
        self.create_programs([('arbitrary_name', 'gift_card')])
        # Change the gift card program settings
        self.main_pos_config.write({'gift_card_settings': 'create_set'})
        self.start_pos_tour("GiftCardWithRefundtTour")

    def test_loyalty_program_specific_product(self):
        #create a loyalty program with a rules of minimum 2 qty that applies on produt A and B and reward 5 points. The reward is 10$ per order in exchange of 2 points on product A and B
        LoyaltyProgram = self.env['loyalty.program']
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        self.product_a = self.env["product.product"].create({
            "name": "Test Product A",
            "is_storable": True,
            "list_price": 40,
            "available_in_pos": True,
            "taxes_id": False,
        })
        self.product_b = self.env["product.product"].create({
            "name": "Test Product B",
            "is_storable": True,
            "list_price": 40,
            "available_in_pos": True,
            "taxes_id": False,
        })
        self.loyalty_program = self.env['loyalty.program'].create({
            'name': 'Loyalty Program Test',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'pos_ok': True,
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 5,
                'minimum_qty': 2,
                'product_ids': [(6, 0, [self.product_a.id, self.product_b.id])],
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount_mode': 'per_order',
                'required_points': 2,
                'discount': 10,
                'discount_applicability': 'specific',
                'discount_product_ids': (self.product_a | self.product_b).ids,
            })],
        })
        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltySpecificDiscountTour")

    def test_discount_specific_product_with_free_product(self):
        LoyaltyProgram = self.env['loyalty.program']
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        self.product_a = self.env['product.product'].create({
            'name': 'Test Product A',
            'is_storable': True,
            'list_price': 40,
            'available_in_pos': True,
            'taxes_id': False,
        })
        self.product_b = self.env['product.product'].create({
            'name': 'Test Product B',
            'is_storable': True,
            'list_price': 80,
            'available_in_pos': True,
            'taxes_id': False,
        })
        self.product_c = self.env['product.product'].create({
            'name': 'Test Product C',
            'is_storable': True,
            'list_price': 100,
            'available_in_pos': True,
            'taxes_id': False,
        })
        self.env['loyalty.program'].create({
            'name': 'Discount 10%',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
                'minimum_amount': 10,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount_product_ids': self.product_c.ids,
                'required_points': 1,
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.env['loyalty.program'].create({
            'name': 'Buy product_a Take product_b',
            'program_type': 'buy_x_get_y',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'product_ids': self.product_a.ids,
                'reward_point_mode': 'unit',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.product_b.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.main_pos_config.open_ui()
        self.start_pos_tour('PosLoyaltySpecificDiscountWithFreeProductTour')

    def test_point_per_money_spent(self):
        """Test the point per $ spent feature"""
        LoyaltyProgram = self.env['loyalty.program']
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        self.loyalty_program = self.env['loyalty.program'].create({
            'name': 'Loyalty Program Test',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'pricelist_ids': [(4, self.main_pos_config.pricelist_id.id)],
            'pos_ok': True,
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'money',
                'reward_point_amount': 0.1,
                'minimum_amount': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 1,
                'discount_mode': 'per_point',
            })],
        })

        self.product_a = self.env["product.product"].create({
            "name": "Test Product A",
            "is_storable": True,
            "list_price": 265,
            "available_in_pos": True,
            "taxes_id": False,
        })

        partner_aaa = self.env['res.partner'].create({'name': 'AAA Partner'})
        self.env['loyalty.card'].create({
            'partner_id': partner_aaa.id,
            'program_id': self.loyalty_program.id,
            'points': 100,
        })

        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour6")

    def test_coupon_program_without_rules(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        self.env["product.product"].create(
            {
                "name": "Test Product",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": False,
            }
        )

        # creating a coupon program without any rule
        loyalty_program = self.env['loyalty.program'].create({
            'name': 'Coupon Program without rules',
            'program_type': 'coupons',
            'trigger': 'with_code',
            'applies_on': 'current',
            'pos_ok': True,
            'rule_ids': [],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })

        self.env["loyalty.generate.wizard"].with_context(
            {"active_id": loyalty_program.id}
        ).create({"coupon_qty": 1, 'points_granted': 1}).generate_coupons()
        self.coupon1 = loyalty_program.coupon_ids
        self.coupon1.write({"code": "abcda"})

        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour7")

    def test_discount_with_reward_product_domain(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        product_category_base = self.env.ref('product.product_category_1')
        product_category_1 = self.env['product.category'].create({
            'name': 'Office furnitures',
            'parent_id': product_category_base.id
        })

        self.productA = self.env['product.product'].create(
            {
                'name': 'Product A',
                'is_storable': True,
                'list_price': 15,
                'available_in_pos': True,
                'taxes_id': False,
                'categ_id': product_category_base.id
            }
        )

        self.productB = self.env['product.product'].create(
            {
                'name': 'Product B',
                'is_storable': True,
                'list_price': 50,
                'available_in_pos': True,
                'taxes_id': False,
                'categ_id': product_category_1.id
            }
        )

        self.env['loyalty.program'].create({
            'name': 'Discount on Specific Products',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 50,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_domain': '[("categ_id", "ilike", "office")]',
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltySpecificDiscountWithRewardProductDomainTour")

    def test_promotion_program_with_loyalty_program(self):
        """
        - Create a promotion with a discount of 10%
        - Create a loyalty program with a fixed discount of 10€
        - Apply both programs to the order
        - Check that no "infinity" discount is applied
        """
        self.env['loyalty.program'].search([]).write({'active': False})
        self.promo_program = self.env['loyalty.program'].create({
            'name': 'Promo Program',
            'program_type': 'promotion',
            'pos_ok': True,
            'rule_ids': [(0, 0, {
                'minimum_amount': 0,
                'minimum_qty': 0
                })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })

        self.discount_product = self.env["product.product"].create(
            {
                "name": "Discount Product",
                "type": "service",
                "list_price": 0,
                "available_in_pos": True,
                "taxes_id": False,
            }
        )

        self.test_product = self.env["product.product"].create(
            {
                "name": "Test Product 1",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": False,
            }
        )

        self.loyalty_program = self.env["loyalty.program"].create(
            {
                "name": "Loyalty Program",
                "program_type": "loyalty",
                "pos_ok": True,
                "rule_ids": [(0, 0, {
                    "minimum_amount": 1,
                    "minimum_qty": 1,
                    "reward_point_mode": "order",
                    "reward_point_amount": 500,
                })],
                "reward_ids": [(0, 0, {
                    "required_points": 500,
                    "reward_type": "discount",
                    "discount": "10",
                    "discount_mode": "per_order",
                })],
            }
        )

        partner = self.env['res.partner'].create({'name': 'AAA Partner'})
        self.env['loyalty.card'].create({
            'partner_id': partner.id,
            'program_id': self.loyalty_program.id,
            'points': 500,
        })

        self.main_pos_config.open_ui()

        self.start_pos_tour("PosLoyaltyPromotion")

    def test_promo_with_free_product(self):
        self.env['loyalty.program'].search([]).write({'active': False})
        self.tax01 = self.env["account.tax"].create({
            "name": "C01 Tax",
            "amount": "15.00",
        })
        self.product_a = self.env["product.product"].create(
            {
                "name": "Product A",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": [(6, 0, self.tax01.ids)],
            }
        )
        self.product_b = self.env["product.product"].create(
            {
                "name": "Product B",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": False,
            }
        )
        self.free_product = self.env['loyalty.program'].create({
            'name': 'Free Product A',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'unit',
                'minimum_qty': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': self.product_a.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
        })
        self.env['loyalty.program'].create({
            'name': 'Discount 50%',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'reward_point_amount': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 50,
                'discount_mode': 'percent',
            })],
        })
        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour8")

    def test_discount_specific_products(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        product_category_base = self.env.ref('product.product_category_1')
        product_category_1 = self.env['product.category'].create({
            'name': 'Office furnitures',
            'parent_id': product_category_base.id
        })

        self.productA = self.env['product.product'].create(
            {
                'name': 'Product A',
                'is_storable': True,
                'list_price': 15,
                'available_in_pos': True,
                'taxes_id': False,
                'categ_id': product_category_base.id
            }
        )

        self.productB = self.env['product.product'].create(
            {
                'name': 'Product B',
                'is_storable': True,
                'list_price': 50,
                'available_in_pos': True,
                'taxes_id': False,
                'categ_id': product_category_1.id
            }
        )

        self.env['loyalty.program'].create({
            'name': 'Discount on Specific Products',
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'order',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 1,
                'discount': 50,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_category_id': product_category_1.id,
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltySpecificDiscountCategoryTour")

    def test_promo_with_different_taxes(self):
        self.env['loyalty.program'].search([]).write({'active': False})
        self.tax01 = self.env["account.tax"].create({
            "name": "C01 Tax",
            "amount": "10.00",
        })
        self.product_a = self.env["product.product"].create(
            {
                "name": "Product A",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": [(6, 0, self.tax01.ids)],
            }
        )
        self.product_b = self.env["product.product"].create(
            {
                "name": "Product B",
                "is_storable": True,
                "list_price": 100,
                "available_in_pos": True,
                "taxes_id": False,
            }
        )
        self.free_product = self.env['loyalty.program'].create({
            'name': 'Free Product A',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'required_points': 5,
                'discount_mode': 'per_order',
                'discount': 5,
            })],
        })
        self.env['res.partner'].create({'name': 'AAA Partner'})
        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour9")

    def test_ewallet_expiration_date(self):
        LoyaltyProgram = self.env['loyalty.program']
        # Deactivate all other programs to avoid interference
        (LoyaltyProgram.search([])).write({'pos_ok': False})
        # But activate the ewallet_product_50 because it's shared among new ewallet programs.
        self.env.ref('loyalty.ewallet_product_50').write({'active': True})
        # Create ewallet program
        ewallet_program = self.create_programs([('arbitrary_name', 'ewallet')])['arbitrary_name']
        # Create test partners
        partner_aaa = self.env['res.partner'].create({'name': 'AAAA'})
        #Create an eWallet for partner_aaa
        self.env['loyalty.card'].create({
            'partner_id': partner_aaa.id,
            'program_id': ewallet_program.id,
            'points': 50,
            'expiration_date': date(2020, 1, 1),
        })
        self.main_pos_config.open_ui()
        self.start_pos_tour("ExpiredEWalletProgramTour")

    def test_loyalty_program_with_tagged_free_product(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        free_product_tag = self.env['product.tag'].create({'name': 'Free Product'})

        self.env['product.product'].create([
            {
                'name': 'Free Product A',
                'is_storable': True,
                'list_price': 1,
                'available_in_pos': True,
                'taxes_id': False,
                'product_tag_ids': [(4, free_product_tag.id)],
            },
            {
                'name': 'Free Product B',
                'is_storable': True,
                'list_price': 1,
                'available_in_pos': True,
                'taxes_id': False,
                'product_tag_ids': [(4, free_product_tag.id)],
            },
            {
                'name': 'Product Test',
                'is_storable': True,
                'list_price': 1,
                'available_in_pos': True,
                'taxes_id': False,
            }
        ])

        self.env['loyalty.program'].create({
            'name': 'Free Product with Tag',
            'program_type': 'loyalty',
            'applies_on': 'both',
            'trigger': 'auto',
            'portal_visible': True,
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'unit',
                'minimum_qty': 1,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_tag_id': free_product_tag.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
        })

        self.env['res.partner'].create({'name': 'AAA Partner'})
        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour10")

    def test_loyalty_program_with_next_order_coupon_free_product(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        free_product = self.env['product.product'].create({
                'name': 'Free Product',
                'is_storable': True,
                'list_price': 1,
                'available_in_pos': True,
                'taxes_id': False,
            })
        self.env['product.product'].create({
                'name': 'Product Test',
                'is_storable': True,
                'list_price': 50,
                'available_in_pos': True,
                'taxes_id': False,
            })

        loyalty_program = self.env['loyalty.program'].create({
            'name': 'Next Order Coupon Program',
            'program_type': 'next_order_coupons',
            'applies_on': 'future',
            'trigger': 'auto',
            'portal_visible': True,
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'unit',
                'minimum_amount': 100,
                'minimum_qty': 0,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_id': free_product.id,
                'reward_product_qty': 1,
                'required_points': 1,
            })],
        })

        self.env['res.partner'].create({'name': 'AAA Partner'})
        self.pos_user.write({
            'groups_id': [
                (4, self.env.ref('stock.group_stock_user').id),
            ]
        })
        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour11.1")

        coupon = loyalty_program.coupon_ids
        self.assertEqual(len(coupon), 1, "Coupon not generated")
        self.assertEqual(coupon.points, 3, "Coupon not generated with correct points")
        coupon.write({"code": "123456"})

        self.main_pos_config.open_ui()
        self.start_pos_tour("PosLoyaltyTour11.2")
        self.assertEqual(coupon.points, 0, "Coupon not used")

    def test_loyalty_program_with_tagged_buy_x_get_y(self):
        self.env['loyalty.program'].search([]).write({'active': False})

        free_product_tag = self.env['product.tag'].create({'name': 'Free Product'})

        self.env['product.product'].create([
            {
                'name': 'Free Product A',
                'list_price': 1,
                'available_in_pos': True,
                'taxes_id': False,
                'product_tag_ids': [(4, free_product_tag.id)],
            },
            {
                'name': 'Free Product B',
                'list_price': 5,
                'available_in_pos': True,
                'taxes_id': False,
                'product_tag_ids': [(4, free_product_tag.id)],
            },
        ])

        self.env['loyalty.program'].create({
            'name': 'Buy X get Y with Tag',
            'program_type': 'buy_x_get_y',
            'applies_on': 'current',
            'trigger': 'auto',
            'portal_visible': True,
            'rule_ids': [(0, 0, {
                'reward_point_mode': 'unit',
                'minimum_qty': 1,
                'product_tag_id': free_product_tag.id,
            })],
            'reward_ids': [(0, 0, {
                'reward_type': 'product',
                'reward_product_tag_id': free_product_tag.id,
                'reward_product_qty': 1,
                'required_points': 2,
            })],
        })

        self.main_pos_config.open_ui()
        self.start_tour(
            "/pos/web?config_id=%d" % self.main_pos_config.id,
            "PosLoyaltyTour12",
            login="pos_user",
        )

    def test_promotion_with_min_amount_and_specific_product_rule(self):
        """
        Test that the discount is applied iff the min amount is reached for the specified product.
        """
        self.env['loyalty.program'].search([]).action_archive()
        self.product_a = self.env['product.product'].create({
            'name': "Product A",
            'is_storable': True,
            'list_price': 20,
            'available_in_pos': True,
            'taxes_id': False,
        })
        self.env['product.product'].create({
            'name': "Product B",
            'is_storable': True,
            'list_price': 30,
            'available_in_pos': True,
            'taxes_id': False,
        })
        self.env['loyalty.program'].create({
            'name': "Discount on specific products",
            'program_type': 'promotion',
            'trigger': 'auto',
            'applies_on': 'current',
            'rule_ids': [Command.create({
                'minimum_amount': 40,
                'product_ids': [Command.set(self.product_a.ids)],
            })],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount': 10,
                'discount_mode': 'percent',
                'discount_applicability': 'specific',
                'discount_product_ids': [Command.set(self.product_a.ids)],
            })],
            'pos_config_ids': [Command.link(self.main_pos_config.id)],
        })

        self.main_pos_config.open_ui()
        self.start_tour(
            '/pos/web?config_id=%d' % self.main_pos_config.id,
            'PosLoyaltyMinAmountAndSpecificProductTour',
            login='pos_user',
        )
